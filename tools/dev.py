"""Stable, cross-platform developer command dispatcher."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Final

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
GITLEAKS_VERSION: Final = "8.30.1"
MINIMUM_GIT_VERSION: Final = (2, 45)
MAX_SECRET_SCAN_BYTES: Final = 50 * 1024 * 1024
MAX_REPOSITORY_PATH_BYTES: Final = 32_767
MAX_SECRET_SCAN_ITEMS: Final = 10_000
MAX_SECRET_SCAN_METADATA_BYTES: Final = 16 * 1024 * 1024
MAX_SECRET_SCAN_WORK_BYTES: Final = 512 * 1024 * 1024
MAX_SECRET_SCAN_SECONDS: Final = 5 * 60
MAX_SCANNER_DIAGNOSTIC_BYTES: Final = 1024 * 1024
SCANNER_SUBPROCESS_TIMEOUT_SECONDS: Final = 120
TOOL_VERSION_OUTPUT_BYTES: Final = 4096
TOOL_VERSION_TIMEOUT_SECONDS: Final = 10
RAW_SCAN_SENTINEL: Final = b"PaperWorkShell raw-byte scan\n"
MAX_SECRET_SCAN_BATCH_BYTES: Final = MAX_SECRET_SCAN_BYTES + len(RAW_SCAN_SENTINEL) + 1
TRUSTED_GITLEAKS_CONFIG_NAME: Final = "trusted-gitleaks.toml"
WINDOWS_FORBIDDEN_COMPONENT_CHARACTERS: Final = frozenset('<>:"\\|?*')
WINDOWS_RESERVED_BASENAMES: Final = frozenset(
    {
        "aux",
        "con",
        "conin$",
        "conout$",
        "nul",
        "prn",
        *(f"com{number}" for number in range(1, 10)),
        *(f"lpt{number}" for number in range(1, 10)),
        "com¹",
        "com²",
        "com³",
        "lpt¹",
        "lpt²",
        "lpt³",
    }
)
WINDOWS_PROTECTED_GIT_COMPONENTS: Final = frozenset({".git", "git~1"})
PINNED_PATH_RULE_SUFFIXES: Final = (
    ".php",
    ".tf",
    ".hcl",
    ".yaml",
    ".yml",
    ".p12",
    ".pfx",
)
SCANNER_GIT_ENVIRONMENT_BLOCKLIST: Final = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CEILING_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_COMMIT_GRAPH_PARANOIA",
        "GIT_CONFIG",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_SYSTEM",
        "GIT_DIR",
        "GIT_DISCOVERY_ACROSS_FILESYSTEM",
        "GIT_EXEC_PATH",
        "GIT_EXTERNAL_DIFF",
        "GIT_GRAFT_FILE",
        "GIT_IMPLICIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_NAMESPACE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_PREFIX",
        "GIT_QUARANTINE_PATH",
        "GIT_REFERENCE_BACKEND",
        "GIT_REF_PARANOIA",
        "GIT_REPLACE_REF_BASE",
        "GIT_SHALLOW_FILE",
        "GIT_WORK_TREE",
    }
)


class CommandMode(Enum):
    """How a developer command is executed."""

    UV = "uv"
    GITLEAKS = "gitleaks"


@dataclass(frozen=True)
class CommandSpec:
    """One stable developer command and its quality-suite membership."""

    arguments: tuple[str, ...]
    description: str
    include_in_check: bool = True
    mode: CommandMode = CommandMode.UV


COMMANDS: Final[dict[str, CommandSpec]] = {
    "format": CommandSpec(
        ("ruff", "format", "."),
        "Apply repository formatting.",
        include_in_check=False,
    ),
    "format-check": CommandSpec(
        ("ruff", "format", "--check", "."),
        "Check formatting without changing files.",
    ),
    "lint": CommandSpec(("ruff", "check", "."), "Run static lint checks."),
    "check-import-boundaries": CommandSpec(
        ("python", "tools/check_import_boundaries.py"),
        "Validate package layout and architecture imports.",
    ),
    "typecheck": CommandSpec(
        ("mypy", "src", "tools", "tests"),
        "Run strict static type checking.",
    ),
    "test-unit": CommandSpec(("pytest", "tests/unit"), "Run unit tests."),
    "test-integration": CommandSpec(
        ("pytest", "tests/integration"),
        "Run cross-component and command integration tests.",
    ),
    "test-security": CommandSpec(
        ("pytest", "tests/security"),
        "Run security-focused tests.",
    ),
    "test-accessibility": CommandSpec(
        ("pytest", "tests/accessibility"),
        "Run accessibility-focused tests.",
    ),
    "generate-fixtures": CommandSpec(
        ("python", "-m", "tools.generate_synthetic_fixtures", "regenerate"),
        "Regenerate the reviewed public synthetic document corpus.",
        include_in_check=False,
    ),
    "validate-fixtures": CommandSpec(
        ("python", "-m", "tools.validate_synthetic_fixtures"),
        "Validate synthetic fixture bytes, provenance, and repository document policy.",
    ),
    "validate-schemas": CommandSpec(
        ("python", "-m", "tools.validate_schemas"),
        "Validate every registered repository schema and instance.",
    ),
    "validate-dependencies": CommandSpec(
        ("python", "-m", "tools.validate_dependencies"),
        "Validate dependency, license, provenance, and redistribution policy.",
        include_in_check=False,
    ),
    "validate-task-graph": CommandSpec(
        ("python", "tools/validate_tasks.py"),
        "Validate task references, gates, and the dependency DAG.",
    ),
    "validate-docs": CommandSpec(
        ("python", "-m", "tools.validate_docs"),
        "Validate local documentation links and security references offline.",
    ),
    "validate-reference-profile": CommandSpec(
        ("python", "-m", "tools.reference_profile", "validate"),
        "Validate the reference Windows environment and workload profile.",
    ),
    "scan-secrets": CommandSpec(
        (),
        "Scan Git history and the local tree for secrets without uploading content.",
        mode=CommandMode.GITLEAKS,
    ),
    "quality-self-test": CommandSpec(
        ("python", "-m", "tools.quality_self_test"),
        "Prove deliberate quality violations are rejected.",
    ),
    "benchmark-reference": CommandSpec(
        ("python", "-m", "tools.reference_profile", "benchmark"),
        "Measure the baseline workload without applying guessed targets.",
        include_in_check=False,
    ),
}

CHECK_COMMANDS: Final = tuple(
    name for name, command in COMMANDS.items() if command.include_in_check
)

GITLEAKS_STDIN_ARGUMENTS: Final = (
    "stdin",
    "--no-banner",
    "--no-color",
    "--ignore-gitleaks-allow",
    "--redact=100",
    "--exit-code",
    "42",
    "--timeout",
    "120",
)


class ToolRequirementError(Exception):
    """Raised when a required local developer tool is missing or mismatched."""


class SecretScanError(Exception):
    """A sanitized, expected secret-scanning failure."""

    def __init__(self, code: str, message: str, *, exit_code: int = 1) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code


@dataclass(frozen=True)
class _PathBlobEntry:
    relative_path: Path
    object_id: str
    object_size: int


@dataclass(frozen=True)
class _SecretScanSummary:
    history_blob_count: int
    history_message_object_count: int
    historical_path_count: int
    index_count: int
    tracked_count: int
    untracked_count: int


@dataclass(frozen=True)
class RepositoryContentSummary:
    """Counts for one hardened, callback-inspected repository snapshot."""

    history_blob_count: int
    history_message_object_count: int
    historical_path_count: int
    index_blob_count: int
    tracked_count: int
    untracked_count: int


@dataclass(frozen=True)
class _GitHistoryRoots:
    worktree_roots: tuple[Path, ...]
    ref_names: tuple[bytes, ...]
    ordinary_object_ids: tuple[str, ...]
    extra_object_ids: tuple[str, ...]
    revision_ids: tuple[str, ...]
    commit_ids: tuple[str, ...]
    tree_ids: tuple[str, ...]
    blob_ids: tuple[str, ...]


@dataclass
class _SecretScanEnumerationBudget:
    started_at: float = field(default_factory=time.monotonic)
    metadata_bytes: int = 0
    records: int = 0
    work_bytes: int = 0

    def checkpoint(self) -> None:
        if time.monotonic() - self.started_at >= MAX_SECRET_SCAN_SECONDS:
            raise SecretScanError(
                "SECRET_SCAN_TIMEOUT",
                "Repository scanning exceeded the five-minute time limit.",
            )

    def subprocess_timeout(self, maximum: float) -> float:
        self.checkpoint()
        remaining = MAX_SECRET_SCAN_SECONDS - (time.monotonic() - self.started_at)
        if remaining <= 0:
            raise SecretScanError(
                "SECRET_SCAN_TIMEOUT",
                "Repository scanning exceeded the five-minute time limit.",
            )
        return min(maximum, remaining)

    def ensure_work_capacity(self, byte_count: int) -> None:
        self.checkpoint()
        if byte_count < 0 or self.work_bytes + byte_count > MAX_SECRET_SCAN_WORK_BYTES:
            raise SecretScanError(
                "SECRET_SCAN_INPUT_TOO_LARGE",
                "Repository scanning exceeds the 512 MiB logical-work limit.",
            )

    def consume_work(self, byte_count: int) -> None:
        self.ensure_work_capacity(byte_count)
        self.work_bytes += byte_count

    @property
    def remaining_metadata_bytes(self) -> int:
        return MAX_SECRET_SCAN_METADATA_BYTES - self.metadata_bytes

    def consume_metadata(self, content: bytes) -> None:
        self.checkpoint()
        if len(content) > self.remaining_metadata_bytes:
            raise SecretScanError(
                "SECRET_SCAN_INPUT_TOO_LARGE",
                "Git metadata exceeds the 16 MiB scanner limit.",
            )
        self.metadata_bytes += len(content)

    def consume_records(self, content: bytes, separator: bytes) -> None:
        self.checkpoint()
        record_count = content.count(separator)
        if content and not content.endswith(separator):
            record_count += 1
        if self.records + record_count > MAX_SECRET_SCAN_ITEMS:
            raise SecretScanError(
                "SECRET_SCAN_INPUT_TOO_LARGE",
                f"repository enumeration exceeds the {MAX_SECRET_SCAN_ITEMS:,}-item scanner limit.",
            )
        self.records += record_count


def _trusted_gitleaks_config(trusted_ignore: Path) -> Path:
    return trusted_ignore.with_name(TRUSTED_GITLEAKS_CONFIG_NAME)


def _with_trusted_controls(arguments: Sequence[str], trusted_ignore: Path) -> tuple[str, ...]:
    return (
        arguments[0],
        "--gitleaks-ignore-path",
        str(trusted_ignore),
        "--config",
        str(_trusted_gitleaks_config(trusted_ignore)),
        *arguments[1:],
    )


def _required_executable(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise ToolRequirementError(name)
    return executable


def _run(arguments: Sequence[str]) -> int:
    displayed_arguments = [Path(arguments[0]).name, *arguments[1:]]
    rendered = subprocess.list2cmdline(displayed_arguments)
    print(f"> {rendered}", flush=True)
    completed = subprocess.run(  # noqa: S603 - arguments come from the fixed registry above
        list(arguments),
        cwd=PROJECT_ROOT,
        check=False,
        shell=False,
    )
    return completed.returncode


def _gitleaks_executable(budget: _SecretScanEnumerationBudget | None = None) -> str:
    executable = _required_executable("gitleaks")
    timeout = (
        TOOL_VERSION_TIMEOUT_SECONDS
        if budget is None
        else budget.subprocess_timeout(TOOL_VERSION_TIMEOUT_SECONDS)
    )
    try:
        completed = _capture_bytes(
            (executable, "version"),
            cwd=PROJECT_ROOT,
            stdout_limit=TOOL_VERSION_OUTPUT_BYTES,
            timeout_seconds=timeout,
        )
        actual_version = completed.stdout.decode("ascii").strip()
    except (OSError, SecretScanError, UnicodeDecodeError) as error:
        if budget is not None:
            budget.checkpoint()
        raise ToolRequirementError("gitleaks") from error
    if completed.returncode != 0 or actual_version != GITLEAKS_VERSION:
        raise ToolRequirementError("gitleaks")
    return executable


def _git_executable(budget: _SecretScanEnumerationBudget | None = None) -> str:
    executable = _required_executable("git")
    timeout = (
        TOOL_VERSION_TIMEOUT_SECONDS
        if budget is None
        else budget.subprocess_timeout(TOOL_VERSION_TIMEOUT_SECONDS)
    )
    try:
        completed = _capture_bytes(
            (executable, "--version"),
            cwd=PROJECT_ROOT,
            stdout_limit=TOOL_VERSION_OUTPUT_BYTES,
            timeout_seconds=timeout,
        )
        actual_version = completed.stdout.decode("ascii").strip()
    except (OSError, SecretScanError, UnicodeDecodeError) as error:
        if budget is not None:
            budget.checkpoint()
        raise ToolRequirementError("git") from error
    prefix = "git version "
    try:
        fields = actual_version.removeprefix(prefix).split(".", maxsplit=2)
        parsed_version = (int(fields[0]), int(fields[1]))
    except IndexError, ValueError:
        parsed_version = (0, 0)
    if (
        completed.returncode != 0
        or not actual_version.startswith(prefix)
        or parsed_version < MINIMUM_GIT_VERSION
    ):
        raise ToolRequirementError("git")
    return executable


def _assign_windows_kill_job(process: subprocess.Popen[bytes]) -> int:
    import ctypes
    from ctypes import wintypes

    class IoCounters(ctypes.Structure):
        _fields_ = [
            ("read_operation_count", ctypes.c_ulonglong),
            ("write_operation_count", ctypes.c_ulonglong),
            ("other_operation_count", ctypes.c_ulonglong),
            ("read_transfer_count", ctypes.c_ulonglong),
            ("write_transfer_count", ctypes.c_ulonglong),
            ("other_transfer_count", ctypes.c_ulonglong),
        ]

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("per_process_user_time_limit", ctypes.c_longlong),
            ("per_job_user_time_limit", ctypes.c_longlong),
            ("limit_flags", wintypes.DWORD),
            ("minimum_working_set_size", ctypes.c_size_t),
            ("maximum_working_set_size", ctypes.c_size_t),
            ("active_process_limit", wintypes.DWORD),
            ("affinity", ctypes.c_size_t),
            ("priority_class", wintypes.DWORD),
            ("scheduling_class", wintypes.DWORD),
        ]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("basic_limit_information", BasicLimitInformation),
            ("io_info", IoCounters),
            ("process_memory_limit", ctypes.c_size_t),
            ("job_memory_limit", ctypes.c_size_t),
            ("peak_process_memory_used", ctypes.c_size_t),
            ("peak_job_memory_used", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_job = kernel32.CreateJobObjectW
    create_job.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    create_job.restype = wintypes.HANDLE
    set_job_information = kernel32.SetInformationJobObject
    set_job_information.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    set_job_information.restype = wintypes.BOOL
    assign_process = kernel32.AssignProcessToJobObject
    assign_process.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    assign_process.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    job = create_job(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "could not create scanner job")
    information = ExtendedLimitInformation()
    information.basic_limit_information.limit_flags = 0x00002000
    try:
        if not set_job_information(job, 9, ctypes.byref(information), ctypes.sizeof(information)):
            raise OSError(ctypes.get_last_error(), "could not configure scanner job")
        process_handle = wintypes.HANDLE(int(process._handle))  # type: ignore[attr-defined]
        if not assign_process(job, process_handle):
            raise OSError(ctypes.get_last_error(), "could not isolate scanner subprocess")
    except BaseException:
        close_handle(job)
        raise
    return int(job)


def _resume_windows_process(process: subprocess.Popen[bytes]) -> None:
    import ctypes
    from ctypes import wintypes

    resume_process = ctypes.WinDLL("ntdll").NtResumeProcess
    resume_process.argtypes = (wintypes.HANDLE,)
    resume_process.restype = ctypes.c_long
    process_handle = wintypes.HANDLE(int(process._handle))  # type: ignore[attr-defined]
    if resume_process(process_handle) != 0:
        raise OSError("could not start isolated scanner subprocess")


def _close_windows_job(job_handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    close_handle = ctypes.WinDLL("kernel32").CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    if not close_handle(wintypes.HANDLE(job_handle)):
        raise OSError("could not close scanner job")


def _scanner_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in tuple(environment):
        if (
            name in SCANNER_GIT_ENVIRONMENT_BLOCKLIST
            or name.startswith("GIT_CONFIG_KEY_")
            or name.startswith("GIT_CONFIG_VALUE_")
            or name.startswith("GIT_TEST_")
        ):
            environment.pop(name)
    environment["GIT_NO_LAZY_FETCH"] = "1"
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GIT_CONFIG_COUNT"] = "2"
    environment["GIT_CONFIG_KEY_0"] = "core.commitGraph"
    environment["GIT_CONFIG_VALUE_0"] = "false"
    environment["GIT_CONFIG_KEY_1"] = "core.fsmonitor"
    environment["GIT_CONFIG_VALUE_1"] = "false"
    return environment


def _capture_bytes(
    arguments: Sequence[str],
    *,
    cwd: Path,
    input_bytes: bytes | None = None,
    stdout_limit: int = MAX_SCANNER_DIAGNOSTIC_BYTES,
    timeout_seconds: float = SCANNER_SUBPROCESS_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[bytes]:
    environment = _scanner_environment()
    if stdout_limit < 0:
        raise ValueError("stdout_limit must not be negative")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    windows = os.name == "nt"
    process = subprocess.Popen(  # noqa: S603 - fixed arrays, sanitized environment, no shell
        list(arguments),
        cwd=cwd,
        shell=False,
        stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=environment,
        bufsize=0,
        creationflags=0x00000004 if windows else 0,
        start_new_session=not windows,
    )
    windows_job: int | None = None
    tree_lock = threading.Lock()
    tree_closed = threading.Event()
    write_failed = threading.Event()
    write_complete = threading.Event()
    read_failed = threading.Event()
    read_complete = threading.Event()
    overflowed = threading.Event()
    output = bytearray()

    def terminate_process_tree() -> None:
        nonlocal windows_job
        with tree_lock:
            if tree_closed.is_set():
                return
            tree_closed.set()
            if windows_job is not None:
                job_handle = windows_job
                windows_job = None
                try:
                    _close_windows_job(job_handle)
                except OSError:
                    pass
            elif not windows:
                kill_process_group = os.__dict__.get("killpg")
                try:
                    if kill_process_group is not None:
                        kill_process_group(process.pid, 9)
                except ProcessLookupError:
                    pass
                except OSError:
                    pass
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass

    def write_input() -> None:
        if process.stdin is None or input_bytes is None:
            write_complete.set()
            return
        try:
            view = memoryview(input_bytes)
            while view:
                written = process.stdin.write(view)
                if written is None or written <= 0:
                    raise OSError("short scanner-input write")
                view = view[written:]
        except BrokenPipeError, OSError:
            write_failed.set()
        finally:
            try:
                process.stdin.close()
            except OSError:
                write_failed.set()
            write_complete.set()

    def read_output() -> None:
        try:
            if process.stdout is None:
                raise OSError("scanner stdout is unavailable")
            while len(output) <= stdout_limit:
                chunk = process.stdout.read(min(64 * 1024, stdout_limit + 1 - len(output)))
                if not chunk:
                    break
                output.extend(chunk)
            if len(output) > stdout_limit:
                overflowed.set()
                terminate_process_tree()
        except OSError:
            if not tree_closed.is_set():
                read_failed.set()
        finally:
            read_complete.set()

    writer = threading.Thread(target=write_input, name="pws-scan-input", daemon=True)
    reader = threading.Thread(target=read_output, name="pws-scan-output", daemon=True)
    try:
        if windows:
            windows_job = _assign_windows_kill_job(process)
        reader.start()
        writer.start()
        if windows:
            _resume_windows_process(process)
        deadline = time.monotonic() + timeout_seconds
        try:
            returncode = process.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as error:
            terminate_process_tree()
            raise SecretScanError(
                "SECRET_SCAN_TOOL_FAILED", "A scanner subprocess exceeded its time limit."
            ) from error
        terminate_process_tree()
        remaining = max(0.0, deadline - time.monotonic())
        if not read_complete.wait(remaining) or not write_complete.wait(
            max(0.0, deadline - time.monotonic())
        ):
            raise SecretScanError(
                "SECRET_SCAN_TOOL_FAILED", "A scanner subprocess exceeded its time limit."
            )
    except BaseException:
        terminate_process_tree()
        if windows and process.poll() is None:
            try:
                _resume_windows_process(process)
            except OSError:
                pass
        raise
    finally:
        terminate_process_tree()
        if process.poll() is None:
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        if read_complete.is_set() and process.stdout is not None:
            process.stdout.close()
        if write_complete.is_set() and process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
    if write_failed.is_set():
        raise SecretScanError("SECRET_SCAN_TOOL_FAILED", "Scanner input could not be delivered.")
    if read_failed.is_set():
        raise SecretScanError("SECRET_SCAN_TOOL_FAILED", "Scanner output could not be collected.")
    if overflowed.is_set():
        raise SecretScanError(
            "SECRET_SCAN_INPUT_TOO_LARGE",
            "Scanner subprocess output exceeds its bounded limit.",
        )
    return subprocess.CompletedProcess(list(arguments), returncode, bytes(output), b"")


def _capture_git_metadata(
    budget: _SecretScanEnumerationBudget,
    arguments: Sequence[str],
    *,
    cwd: Path,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    completed = _capture_bytes(
        arguments,
        cwd=cwd,
        input_bytes=input_bytes,
        stdout_limit=budget.remaining_metadata_bytes,
        timeout_seconds=budget.subprocess_timeout(SCANNER_SUBPROCESS_TIMEOUT_SECONDS),
    )
    budget.consume_metadata(completed.stdout)
    return completed


def _git_paths(
    git: str,
    root: Path,
    arguments: Sequence[str],
    budget: _SecretScanEnumerationBudget,
) -> list[Path]:
    completed = _capture_git_metadata(budget, (git, *arguments, "-z"), cwd=root)
    if completed.returncode != 0:
        raise SecretScanError("SECRET_SCAN_GIT_FAILED", "Git source enumeration failed.")
    budget.consume_records(completed.stdout, b"\0")
    return [
        _repository_relative_path(item, f"Git source item {index}")
        for index, item in enumerate(
            (encoded for encoded in completed.stdout.split(b"\0") if encoded),
            start=1,
        )
    ]


def _validate_windows_component(component: str, source_label: str) -> None:
    try:
        utf16_units = len(component.encode("utf-16-le")) // 2
    except UnicodeEncodeError as error:
        raise SecretScanError(
            "SECRET_SCAN_INPUT_INVALID",
            f"{source_label} cannot be represented safely on Windows.",
        ) from error
    basename = component.split(".", maxsplit=1)[0].casefold()
    if (
        not component
        or utf16_units > 255
        or component.endswith((" ", "."))
        or basename in WINDOWS_RESERVED_BASENAMES
        or component.casefold() in WINDOWS_PROTECTED_GIT_COMPONENTS
        or any(ord(character) < 32 for character in component)
        or any(character in WINDOWS_FORBIDDEN_COMPONENT_CHARACTERS for character in component)
    ):
        raise SecretScanError(
            "SECRET_SCAN_INPUT_INVALID",
            f"{source_label} cannot be represented safely on Windows.",
        )


def _repository_relative_path(encoded_path: bytes, source_label: str) -> Path:
    if len(encoded_path) > MAX_REPOSITORY_PATH_BYTES:
        raise SecretScanError(
            "SECRET_SCAN_INPUT_INVALID",
            f"{source_label} cannot be represented safely on Windows.",
        )
    decoded = os.fsdecode(encoded_path)
    if "\\" in decoded:
        raise SecretScanError(
            "SECRET_SCAN_INPUT_INVALID",
            f"{source_label} cannot be represented safely on Windows.",
        )
    posix_path = PurePosixPath(decoded)
    if (
        posix_path.is_absolute()
        or not posix_path.parts
        or posix_path.as_posix() != decoded
        or any(part in {".", ".."} for part in posix_path.parts)
    ):
        raise SecretScanError("SECRET_SCAN_INPUT_INVALID", f"{source_label} has an unsafe path.")
    if any(component.casefold() == ".gitleaksignore" for component in posix_path.parts):
        raise SecretScanError(
            "SECRET_SCAN_POLICY_INVALID",
            "source-controlled Gitleaks ignore files are not permitted.",
        )
    for component in posix_path.parts:
        _validate_windows_component(component, source_label)
    return Path(*posix_path.parts)


def _read_scan_input(
    root: Path,
    relative_path: Path,
    *,
    allow_missing: bool,
    source_label: str,
    work_budget: _SecretScanEnumerationBudget | None = None,
) -> bytes | None:
    candidate = root / relative_path
    try:
        metadata = candidate.lstat()
    except FileNotFoundError as error:
        if allow_missing:
            return None
        raise SecretScanError(
            "SECRET_SCAN_INPUT_INVALID", f"{source_label} is unreadable."
        ) from error
    except OSError as error:
        raise SecretScanError(
            "SECRET_SCAN_INPUT_INVALID", f"{source_label} is unreadable."
        ) from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise SecretScanError("SECRET_SCAN_INPUT_INVALID", f"{source_label} is unsafe.")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise SecretScanError(
            "SECRET_SCAN_INPUT_INVALID", f"{source_label} is unreadable."
        ) from error
    if not resolved.is_relative_to(root):
        raise SecretScanError("SECRET_SCAN_INPUT_INVALID", f"{source_label} is unsafe.")
    try:
        with candidate.open("rb") as stream:
            opened_metadata = os.fstat(stream.fileno())
            if (
                opened_metadata.st_dev != metadata.st_dev
                or opened_metadata.st_ino != metadata.st_ino
            ):
                raise SecretScanError(
                    "SECRET_SCAN_INPUT_INVALID", f"{source_label} changed during scanning."
                )
            if opened_metadata.st_size > MAX_SECRET_SCAN_BYTES:
                raise SecretScanError(
                    "SECRET_SCAN_INPUT_TOO_LARGE",
                    f"{source_label} exceeds the 50 MiB scanner limit.",
                )
            if work_budget is not None:
                work_budget.consume_work(opened_metadata.st_size)
            content = stream.read(MAX_SECRET_SCAN_BYTES + 1)
            opened_after = os.fstat(stream.fileno())
        final_metadata = candidate.lstat()
        initial_identity = (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
        )
        if (
            candidate.resolve(strict=True) != resolved
            or initial_identity
            != (
                opened_after.st_dev,
                opened_after.st_ino,
                opened_after.st_size,
                opened_after.st_mtime_ns,
            )
            or initial_identity
            != (
                final_metadata.st_dev,
                final_metadata.st_ino,
                final_metadata.st_size,
                final_metadata.st_mtime_ns,
            )
        ):
            raise SecretScanError(
                "SECRET_SCAN_INPUT_INVALID", f"{source_label} changed during scanning."
            )
    except FileNotFoundError as error:
        if allow_missing:
            return None
        raise SecretScanError(
            "SECRET_SCAN_INPUT_INVALID", f"{source_label} is unreadable."
        ) from error
    except SecretScanError:
        raise
    except OSError as error:
        raise SecretScanError(
            "SECRET_SCAN_INPUT_INVALID", f"{source_label} is unreadable."
        ) from error
    if len(content) > MAX_SECRET_SCAN_BYTES:
        raise SecretScanError(
            "SECRET_SCAN_INPUT_TOO_LARGE",
            f"{source_label} exceeds the 50 MiB scanner limit.",
        )
    return content


def _scan_input(
    gitleaks: str,
    root: Path,
    trusted_ignore: Path,
    content: bytes,
    budget: _SecretScanEnumerationBudget,
) -> int:
    budget.consume_work(len(RAW_SCAN_SENTINEL) + len(content))
    completed = _capture_bytes(
        (
            gitleaks,
            *_with_trusted_controls(GITLEAKS_STDIN_ARGUMENTS, trusted_ignore),
        ),
        cwd=root,
        input_bytes=RAW_SCAN_SENTINEL + content,
        stdout_limit=MAX_SCANNER_DIAGNOSTIC_BYTES,
        timeout_seconds=budget.subprocess_timeout(SCANNER_SUBPROCESS_TIMEOUT_SECONDS),
    )
    return completed.returncode


class _SecretScanBatcher:
    """Deduplicate and batch bounded raw inputs, isolating only findings."""

    def __init__(
        self,
        gitleaks: str,
        root: Path,
        trusted_ignore: Path,
        budget: _SecretScanEnumerationBudget,
    ) -> None:
        self._gitleaks = gitleaks
        self._root = root
        self._trusted_ignore = trusted_ignore
        self._budget = budget
        self._seen_digests: set[bytes] = set()
        self._items: list[tuple[str, bytes]] = []
        self._payload_size = 0

    def add(self, source_label: str, content: bytes) -> None:
        if len(content) > MAX_SECRET_SCAN_BYTES:
            raise SecretScanError(
                "SECRET_SCAN_INPUT_TOO_LARGE",
                f"{source_label} exceeds the 50 MiB scanner limit.",
            )
        digest = hashlib.sha256(content).digest()
        if digest in self._seen_digests:
            return
        self._seen_digests.add(digest)
        item_size = len(RAW_SCAN_SENTINEL) + len(content) + 1
        self._budget.consume_work(item_size)
        if self._items and self._payload_size + item_size > MAX_SECRET_SCAN_BATCH_BYTES:
            self.flush()
        self._items.append((source_label, content))
        self._payload_size += item_size

    def flush(self) -> None:
        if not self._items:
            return
        payload = b"\n".join(RAW_SCAN_SENTINEL + content for _, content in self._items)
        completed = _capture_bytes(
            (
                self._gitleaks,
                *_with_trusted_controls(GITLEAKS_STDIN_ARGUMENTS, self._trusted_ignore),
            ),
            cwd=self._root,
            input_bytes=payload,
            stdout_limit=MAX_SCANNER_DIAGNOSTIC_BYTES,
            timeout_seconds=self._budget.subprocess_timeout(SCANNER_SUBPROCESS_TIMEOUT_SECONDS),
        )
        if completed.returncode == 42:
            for source_label, content in self._items:
                isolated_result = _scan_input(
                    self._gitleaks,
                    self._root,
                    self._trusted_ignore,
                    content,
                    self._budget,
                )
                if isolated_result == 42:
                    raise SecretScanError(
                        "SECRET_SCAN_FINDING",
                        f"{source_label} contains a potential secret.",
                        exit_code=42,
                    )
                if isolated_result != 0:
                    raise SecretScanError(
                        "SECRET_SCAN_TOOL_FAILED",
                        f"Scanner failed for {source_label}.",
                    )
            raise SecretScanError(
                "SECRET_SCAN_FINDING",
                "a raw-input batch contains a potential secret.",
                exit_code=42,
            )
        if completed.returncode != 0:
            raise SecretScanError("SECRET_SCAN_TOOL_FAILED", "Raw repository-byte scanning failed.")
        self._items.clear()
        self._payload_size = 0


def _object_id(encoded: bytes) -> str:
    if len(encoded) not in {40, 64} or any(byte not in b"0123456789abcdef" for byte in encoded):
        raise SecretScanError(
            "SECRET_SCAN_GIT_FAILED", "Git object enumeration returned invalid data."
        )
    return encoded.decode("ascii")


def _git_object_metadata(
    git: str,
    root: Path,
    object_ids: Sequence[str],
    budget: _SecretScanEnumerationBudget,
) -> list[tuple[str, str, int]]:
    if not object_ids:
        return []
    request = ("\n".join(object_ids) + "\n").encode("ascii")
    completed = _capture_git_metadata(
        budget,
        (git, "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"),
        cwd=root,
        input_bytes=request,
    )
    if completed.returncode != 0:
        raise SecretScanError("SECRET_SCAN_GIT_FAILED", "Git object inspection failed.")
    budget.consume_records(completed.stdout, b"\n")
    records = completed.stdout.splitlines()
    if len(records) != len(object_ids):
        raise SecretScanError(
            "SECRET_SCAN_GIT_FAILED", "Git object inspection returned incomplete data."
        )
    metadata: list[tuple[str, str, int]] = []
    for expected_id, record in zip(object_ids, records, strict=True):
        fields = record.split(b" ")
        if len(fields) != 3 or _object_id(fields[0]) != expected_id:
            raise SecretScanError(
                "SECRET_SCAN_GIT_FAILED", "Git object inspection returned invalid data."
            )
        try:
            object_type = fields[1].decode("ascii")
            object_size = int(fields[2])
        except (UnicodeDecodeError, ValueError) as error:
            raise SecretScanError(
                "SECRET_SCAN_GIT_FAILED", "Git object inspection returned invalid data."
            ) from error
        if object_size < 0:
            raise SecretScanError(
                "SECRET_SCAN_GIT_FAILED", "Git object inspection returned invalid data."
            )
        metadata.append((expected_id, object_type, object_size))
    return metadata


def _scan_git_blobs(
    git: str,
    root: Path,
    blobs: Sequence[tuple[str, int]],
    *,
    category: str,
    batcher: _SecretScanBatcher,
    budget: _SecretScanEnumerationBudget,
) -> int:
    for index, (object_id, object_size) in enumerate(blobs, start=1):
        source_label = f"{category} item {index}"
        content = _read_git_blob(git, root, object_id, object_size, source_label, budget)
        batcher.add(source_label, content)
    return len(blobs)


def _scan_git_message_objects(
    git: str,
    root: Path,
    objects: Sequence[tuple[str, str, int]],
    *,
    batcher: _SecretScanBatcher,
    budget: _SecretScanEnumerationBudget,
) -> int:
    for index, (object_id, object_type, object_size) in enumerate(objects, start=1):
        source_label = f"historical commit/tag object {index}"
        content = _read_git_object(
            git,
            root,
            object_id,
            object_type,
            object_size,
            source_label,
            budget,
        )
        batcher.add(source_label, content)
    return len(objects)


def _read_git_blob(
    git: str,
    root: Path,
    object_id: str,
    object_size: int,
    source_label: str,
    budget: _SecretScanEnumerationBudget,
) -> bytes:
    return _read_git_object(git, root, object_id, "blob", object_size, source_label, budget)


def _read_git_object(
    git: str,
    root: Path,
    object_id: str,
    object_type: str,
    object_size: int,
    source_label: str,
    budget: _SecretScanEnumerationBudget,
) -> bytes:
    if object_size > MAX_SECRET_SCAN_BYTES:
        raise SecretScanError(
            "SECRET_SCAN_INPUT_TOO_LARGE",
            f"{source_label} exceeds the 50 MiB scanner limit.",
        )
    if object_type not in {"blob", "commit", "tag"}:
        raise SecretScanError("SECRET_SCAN_GIT_FAILED", f"Git could not read {source_label}.")
    budget.consume_work(object_size)
    completed = _capture_bytes(
        (git, "cat-file", object_type, object_id),
        cwd=root,
        stdout_limit=object_size,
        timeout_seconds=budget.subprocess_timeout(SCANNER_SUBPROCESS_TIMEOUT_SECONDS),
    )
    if completed.returncode != 0 or len(completed.stdout) != object_size:
        raise SecretScanError("SECRET_SCAN_GIT_FAILED", f"Git could not read {source_label}.")
    return completed.stdout


def _read_git_control_file(
    git: str,
    root: Path,
    control_path: str,
    budget: _SecretScanEnumerationBudget,
) -> bytes | None:
    located = _capture_git_metadata(
        budget,
        (git, "rev-parse", "--path-format=absolute", "--git-path", control_path),
        cwd=root,
    )
    budget.consume_records(located.stdout, b"\n")
    path_records = [record for record in located.stdout.splitlines() if record]
    if located.returncode != 0 or len(path_records) != 1 or b"\0" in path_records[0]:
        raise SecretScanError("SECRET_SCAN_GIT_FAILED", "Git root-ref metadata lookup failed.")
    candidate = Path(os.fsdecode(path_records[0]))
    if not candidate.is_absolute():
        candidate = root / candidate
    expected_name = Path(control_path).name
    if candidate.name != expected_name:
        raise SecretScanError("SECRET_SCAN_GIT_FAILED", "Git root-ref metadata path is invalid.")
    if not candidate.parent.exists():
        return None
    try:
        control_root = candidate.parent.resolve(strict=True)
    except OSError as error:
        raise SecretScanError(
            "SECRET_SCAN_GIT_FAILED", "Git root-ref metadata path is invalid."
        ) from error
    content = _read_scan_input(
        control_root,
        Path(expected_name),
        allow_missing=True,
        source_label="Git root-ref metadata",
        work_budget=budget,
    )
    if content is not None:
        budget.consume_metadata(content)
        budget.consume_records(content, b"\n")
    return content


def _git_control_object_ids(
    git: str,
    root: Path,
    control_name: str,
    *,
    tab_delimited: bool,
    budget: _SecretScanEnumerationBudget,
) -> set[str]:
    content = _read_git_control_file(git, root, control_name, budget)
    if content is None:
        return set()
    object_ids: set[str] = set()
    for record in content.splitlines():
        if not record:
            continue
        if tab_delimited:
            try:
                encoded_object_id, _ = record.split(b"\t", maxsplit=1)
            except ValueError as error:
                raise SecretScanError(
                    "SECRET_SCAN_GIT_FAILED", "Git root-ref metadata is invalid."
                ) from error
        else:
            encoded_object_id = record
        object_ids.add(_object_id(encoded_object_id))
    return object_ids


def _require_complete_git_history(
    git: str,
    root: Path,
    budget: _SecretScanEnumerationBudget,
) -> None:
    shallow = _capture_git_metadata(
        budget,
        (git, "rev-parse", "--is-shallow-repository"),
        cwd=root,
    )
    if shallow.returncode != 0 or shallow.stdout.strip() not in {b"false", b"true"}:
        raise SecretScanError("SECRET_SCAN_GIT_FAILED", "Git history state inspection failed.")
    if shallow.stdout.strip() == b"true":
        raise SecretScanError(
            "SECRET_SCAN_HISTORY_INCOMPLETE",
            "shallow Git repositories cannot prove complete history.",
        )
    grafts = _read_git_control_file(git, root, "info/grafts", budget)
    if grafts is not None and grafts.strip():
        raise SecretScanError(
            "SECRET_SCAN_POLICY_INVALID",
            "Git graft metadata is not permitted during repository scanning.",
        )


def _require_git_worktree_root(
    git: str,
    root: Path,
    budget: _SecretScanEnumerationBudget,
) -> None:
    completed = _capture_git_metadata(
        budget,
        (git, "rev-parse", "--path-format=absolute", "--show-toplevel"),
        cwd=root,
    )
    budget.consume_records(completed.stdout, b"\n")
    records = [record for record in completed.stdout.splitlines() if record]
    if completed.returncode != 0 or len(records) != 1 or b"\0" in records[0]:
        raise SecretScanError("SECRET_SCAN_GIT_FAILED", "Git worktree inspection failed.")
    try:
        observed_root = Path(os.fsdecode(records[0])).resolve(strict=True)
    except OSError as error:
        raise SecretScanError(
            "SECRET_SCAN_GIT_FAILED", "Git worktree inspection failed."
        ) from error
    if observed_root != root:
        raise SecretScanError(
            "SECRET_SCAN_REPOSITORY_MISMATCH",
            "Git worktree configuration does not match the requested repository root.",
        )


def _registered_worktree_roots(
    git: str,
    root: Path,
    budget: _SecretScanEnumerationBudget,
) -> tuple[Path, ...]:
    completed = _capture_git_metadata(
        budget,
        (git, "worktree", "list", "--porcelain", "-z"),
        cwd=root,
    )
    if completed.returncode != 0:
        raise SecretScanError("SECRET_SCAN_GIT_FAILED", "Git worktree enumeration failed.")
    budget.consume_records(completed.stdout, b"\0")
    worktree_roots: set[Path] = set()
    for record in completed.stdout.split(b"\0"):
        if not record.startswith(b"worktree "):
            continue
        encoded_path = record.removeprefix(b"worktree ")
        if not encoded_path or b"\n" in encoded_path or b"\r" in encoded_path:
            raise SecretScanError("SECRET_SCAN_GIT_FAILED", "Git worktree data is invalid.")
        try:
            worktree_root = Path(os.fsdecode(encoded_path)).resolve(strict=True)
        except OSError as error:
            raise SecretScanError(
                "SECRET_SCAN_GIT_FAILED", "A registered Git worktree is unavailable."
            ) from error
        if not worktree_root.is_dir() or worktree_root in worktree_roots:
            raise SecretScanError("SECRET_SCAN_GIT_FAILED", "Git worktree data is invalid.")
        _require_git_worktree_root(git, worktree_root, budget)
        worktree_roots.add(worktree_root)
    if root not in worktree_roots:
        raise SecretScanError("SECRET_SCAN_GIT_FAILED", "Git worktree data is incomplete.")
    return tuple(sorted(worktree_roots, key=lambda path: os.path.normcase(str(path))))


def _require_local_ref_storage(
    git: str,
    root: Path,
    budget: _SecretScanEnumerationBudget,
) -> None:
    completed = _capture_git_metadata(
        budget,
        (git, "config", "--get-all", "extensions.refStorage"),
        cwd=root,
    )
    budget.consume_records(completed.stdout, b"\n")
    values = [record for record in completed.stdout.splitlines() if record]
    if completed.returncode == 1 and not values:
        return
    if completed.returncode != 0 or len(values) != 1:
        raise SecretScanError("SECRET_SCAN_GIT_FAILED", "Git ref storage inspection failed.")
    try:
        backend = values[0].decode("ascii")
    except UnicodeDecodeError as error:
        raise SecretScanError(
            "SECRET_SCAN_POLICY_INVALID", "Git ref storage configuration is not permitted."
        ) from error
    if backend not in {"files", "reftable"}:
        raise SecretScanError(
            "SECRET_SCAN_POLICY_INVALID",
            "external Git ref storage payloads are not permitted during repository scanning.",
        )


def _peel_object_ids(
    git: str,
    root: Path,
    object_ids: Sequence[str],
    budget: _SecretScanEnumerationBudget,
) -> set[str]:
    if not object_ids:
        return set()
    request = ("\n".join(f"{object_id}^{{}}" for object_id in object_ids) + "\n").encode("ascii")
    completed = _capture_git_metadata(
        budget,
        (git, "cat-file", "--batch-check=%(objectname) %(objecttype)"),
        cwd=root,
        input_bytes=request,
    )
    budget.consume_records(completed.stdout, b"\n")
    records = completed.stdout.splitlines()
    if completed.returncode != 0 or len(records) != len(object_ids):
        raise SecretScanError("SECRET_SCAN_GIT_FAILED", "Git ref inspection failed.")
    peeled_ids: set[str] = set()
    for record in records:
        fields = record.split(b" ")
        if len(fields) != 2 or fields[1] not in {b"blob", b"commit", b"tree"}:
            raise SecretScanError("SECRET_SCAN_GIT_FAILED", "Git ref inspection failed.")
        peeled_ids.add(_object_id(fields[0]))
    return peeled_ids


def _history_roots(
    git: str,
    root: Path,
    budget: _SecretScanEnumerationBudget,
) -> _GitHistoryRoots:
    worktree_roots = _registered_worktree_roots(git, root, budget)
    ref_names: set[bytes] = set()
    ordinary_ids: set[str] = set()
    extra_ids: set[str] = set()
    for worktree_root in worktree_roots:
        completed = _capture_git_metadata(
            budget,
            (
                git,
                "for-each-ref",
                "--include-root-refs",
                "--format=%(refname)%09%(objectname)",
            ),
            cwd=worktree_root,
        )
        if completed.returncode != 0:
            raise SecretScanError("SECRET_SCAN_GIT_FAILED", "Git ref enumeration failed.")
        budget.consume_records(completed.stdout, b"\n")
        for record in (item for item in completed.stdout.splitlines() if item):
            try:
                encoded_ref_name, encoded_object_id = record.split(b"\t", maxsplit=1)
            except ValueError as error:
                raise SecretScanError(
                    "SECRET_SCAN_GIT_FAILED", "Git ref enumeration returned invalid data."
                ) from error
            if not encoded_ref_name:
                raise SecretScanError(
                    "SECRET_SCAN_GIT_FAILED", "Git ref enumeration returned invalid data."
                )
            ref_names.add(encoded_ref_name)
            target = ordinary_ids if encoded_ref_name.startswith(b"refs/") else extra_ids
            target.add(_object_id(encoded_object_id))
        extra_ids.update(
            _git_control_object_ids(
                git,
                worktree_root,
                "FETCH_HEAD",
                tab_delimited=True,
                budget=budget,
            )
        )
        extra_ids.update(
            _git_control_object_ids(
                git,
                worktree_root,
                "MERGE_HEAD",
                tab_delimited=False,
                budget=budget,
            )
        )
    original_metadata = _git_object_metadata(
        git,
        root,
        sorted(ordinary_ids | extra_ids),
        budget,
    )
    original_types = {object_id: object_type for object_id, object_type, _ in original_metadata}
    if any(
        object_type not in {"blob", "commit", "tag", "tree"}
        for object_type in original_types.values()
    ):
        raise SecretScanError("SECRET_SCAN_GIT_FAILED", "Git refs contain an invalid object type.")
    peeled_ordinary = _peel_object_ids(git, root, sorted(ordinary_ids), budget)
    peeled_extra = _peel_object_ids(git, root, sorted(extra_ids), budget) - peeled_ordinary
    metadata = _git_object_metadata(
        git,
        root,
        sorted(peeled_ordinary | peeled_extra),
        budget,
    )
    object_types = {object_id: object_type for object_id, object_type, _ in metadata}
    if any(object_type not in {"blob", "commit", "tree"} for object_type in object_types.values()):
        raise SecretScanError("SECRET_SCAN_GIT_FAILED", "Git refs contain an invalid object type.")
    return _GitHistoryRoots(
        worktree_roots=worktree_roots,
        ref_names=tuple(sorted(ref_names)),
        ordinary_object_ids=tuple(sorted(ordinary_ids)),
        extra_object_ids=tuple(sorted(extra_ids)),
        revision_ids=tuple(
            sorted(
                object_id
                for object_id in ordinary_ids | extra_ids
                if original_types[object_id] in {"commit", "tag"}
            )
        ),
        commit_ids=tuple(
            sorted(
                object_id
                for object_id in peeled_ordinary | peeled_extra
                if object_types[object_id] == "commit"
            )
        ),
        tree_ids=tuple(
            sorted(
                object_id
                for object_id in peeled_ordinary | peeled_extra
                if object_types[object_id] == "tree"
            )
        ),
        blob_ids=tuple(
            sorted(
                object_id
                for object_id in peeled_ordinary | peeled_extra
                if object_types[object_id] == "blob"
            )
        ),
    )


def _historical_objects(
    git: str,
    root: Path,
    roots: _GitHistoryRoots,
    path_entries: Sequence[_PathBlobEntry],
    budget: _SecretScanEnumerationBudget,
) -> tuple[list[tuple[str, int]], list[tuple[str, str, int]]]:
    revision_input = (
        ("\n".join(roots.revision_ids) + "\n").encode("ascii") if roots.revision_ids else b""
    )
    completed = _capture_git_metadata(
        budget,
        (git, "rev-list", "--objects", "--stdin"),
        cwd=root,
        input_bytes=revision_input,
    )
    if completed.returncode != 0:
        raise SecretScanError("SECRET_SCAN_GIT_FAILED", "Git history enumeration failed.")
    budget.consume_records(completed.stdout, b"\n")
    object_ids = {
        _object_id(record.split(b" ", maxsplit=1)[0])
        for record in completed.stdout.splitlines()
        if record
    }
    object_ids.update(roots.ordinary_object_ids)
    object_ids.update(roots.extra_object_ids)
    object_ids.update(roots.blob_ids)
    object_ids.update(entry.object_id for entry in path_entries)
    metadata = _git_object_metadata(git, root, sorted(object_ids), budget)
    if any(object_type not in {"blob", "commit", "tag", "tree"} for _, object_type, _ in metadata):
        raise SecretScanError(
            "SECRET_SCAN_GIT_FAILED", "Git history contains an invalid object type."
        )
    blobs = [
        (object_id, object_size)
        for object_id, object_type, object_size in metadata
        if object_type == "blob"
    ]
    message_objects = [
        (object_id, object_type, object_size)
        for object_id, object_type, object_size in metadata
        if object_type in {"commit", "tag"}
    ]
    return blobs, message_objects


def _direct_tree_path_objects(
    git: str,
    root: Path,
    tree_ids: Sequence[str],
    budget: _SecretScanEnumerationBudget,
) -> set[tuple[str, str]]:
    path_objects: set[tuple[str, str]] = set()
    for tree_number, tree_id in enumerate(tree_ids, start=1):
        completed = _capture_git_metadata(
            budget,
            (git, "ls-tree", "-r", "-z", "--full-tree", tree_id),
            cwd=root,
        )
        if completed.returncode != 0:
            raise SecretScanError("SECRET_SCAN_GIT_FAILED", "Git tree-ref inspection failed.")
        budget.consume_records(completed.stdout, b"\0")
        for entry_number, record in enumerate(
            (item for item in completed.stdout.split(b"\0") if item),
            start=1,
        ):
            try:
                header, encoded_path = record.split(b"\t", maxsplit=1)
                mode, object_type, encoded_object_id = header.split(b" ")
            except ValueError as error:
                raise SecretScanError(
                    "SECRET_SCAN_GIT_FAILED", "Git tree-ref data is invalid."
                ) from error
            if mode == b"160000" or object_type == b"commit":
                raise SecretScanError(
                    "SECRET_SCAN_GIT_FAILED",
                    "Git tree refs containing submodules cannot be scanned.",
                )
            if object_type != b"blob":
                raise SecretScanError("SECRET_SCAN_GIT_FAILED", "Git tree-ref data is invalid.")
            source_label = f"tree-ref source path {tree_number}.{entry_number}"
            relative_path = _repository_relative_path(encoded_path, source_label).as_posix()
            path_objects.add((relative_path, _object_id(encoded_object_id)))
    return path_objects


def _historical_path_entries(
    git: str,
    root: Path,
    roots: _GitHistoryRoots,
    budget: _SecretScanEnumerationBudget,
) -> list[_PathBlobEntry]:
    revision_input = (
        ("\n".join(roots.commit_ids) + "\n").encode("ascii") if roots.commit_ids else b""
    )
    completed = _capture_git_metadata(
        budget,
        (
            git,
            "log",
            "--format=",
            "--raw",
            "-r",
            "-z",
            "--no-renames",
            "--root",
            "--no-abbrev",
            "-m",
            "--stdin",
        ),
        cwd=root,
        input_bytes=revision_input,
    )
    if completed.returncode != 0:
        raise SecretScanError("SECRET_SCAN_GIT_FAILED", "Git history path enumeration failed.")
    budget.consume_records(completed.stdout, b"\0")
    path_objects: set[tuple[str, str]] = set()
    tokens = completed.stdout.split(b"\0")
    token_index = 0
    while token_index < len(tokens):
        header = tokens[token_index].lstrip(b"\r\n")
        token_index += 1
        if not header:
            continue
        if not header.startswith(b":") or token_index >= len(tokens):
            raise SecretScanError("SECRET_SCAN_GIT_FAILED", "Git history path data is invalid.")
        fields = header[1:].split(b" ")
        if len(fields) != 5:
            raise SecretScanError("SECRET_SCAN_GIT_FAILED", "Git history path data is invalid.")
        encoded_path = tokens[token_index]
        token_index += 1
        if not encoded_path:
            raise SecretScanError("SECRET_SCAN_GIT_FAILED", "Git history path data is invalid.")
        relative_path = _repository_relative_path(encoded_path, "historical source path").as_posix()
        for mode, encoded_object_id in ((fields[0], fields[2]), (fields[1], fields[3])):
            if mode == b"000000" or set(encoded_object_id) == {ord("0")}:
                continue
            if mode == b"160000":
                raise SecretScanError(
                    "SECRET_SCAN_GIT_FAILED",
                    "historical Git submodule entries cannot be scanned.",
                )
            path_objects.add((relative_path, _object_id(encoded_object_id)))

    path_objects.update(_direct_tree_path_objects(git, root, roots.tree_ids, budget))

    object_ids = sorted({object_id for _, object_id in path_objects})
    metadata = _git_object_metadata(git, root, object_ids, budget)
    if any(object_type != "blob" for _, object_type, _ in metadata):
        raise SecretScanError(
            "SECRET_SCAN_GIT_FAILED", "Git history contains an invalid object type."
        )
    sizes = {object_id: object_size for object_id, _, object_size in metadata}
    return [
        _PathBlobEntry(
            relative_path=Path(relative_path),
            object_id=object_id,
            object_size=sizes[object_id],
        )
        for relative_path, object_id in sorted(path_objects)
    ]


def _index_entries(
    git: str,
    root: Path,
    budget: _SecretScanEnumerationBudget,
) -> list[_PathBlobEntry]:
    completed = _capture_git_metadata(
        budget,
        (git, "ls-files", "--stage", "-z"),
        cwd=root,
    )
    if completed.returncode != 0:
        raise SecretScanError("SECRET_SCAN_GIT_FAILED", "Git index enumeration failed.")
    budget.consume_records(completed.stdout, b"\0")
    parsed_entries: list[tuple[Path, str]] = []
    seen_paths: set[Path] = set()
    object_ids: set[str] = set()
    for record in (item for item in completed.stdout.split(b"\0") if item):
        try:
            header, encoded_path = record.split(b"\t", maxsplit=1)
            fields = header.split(b" ")
        except ValueError as error:
            raise SecretScanError(
                "SECRET_SCAN_GIT_FAILED", "Git index enumeration returned invalid data."
            ) from error
        if len(fields) != 3:
            raise SecretScanError(
                "SECRET_SCAN_GIT_FAILED", "Git index enumeration returned invalid data."
            )
        if fields[2] != b"0":
            raise SecretScanError(
                "SECRET_SCAN_GIT_FAILED", "unmerged Git index entries cannot be scanned."
            )
        object_id = _object_id(fields[1])
        object_ids.add(object_id)
        relative_path = _repository_relative_path(encoded_path, "index source path")
        if relative_path in seen_paths:
            raise SecretScanError(
                "SECRET_SCAN_GIT_FAILED", "Git index contains duplicate source paths."
            )
        seen_paths.add(relative_path)
        parsed_entries.append((relative_path, object_id))
    metadata = _git_object_metadata(git, root, sorted(object_ids), budget)
    if any(object_type != "blob" for _, object_type, _ in metadata):
        raise SecretScanError(
            "SECRET_SCAN_GIT_FAILED", "Git index contains an invalid object type."
        )
    sizes = {object_id: object_size for object_id, _, object_size in metadata}
    return [
        _PathBlobEntry(
            relative_path=relative_path,
            object_id=object_id,
            object_size=sizes[object_id],
        )
        for relative_path, object_id in parsed_entries
    ]


def _safe_mirror_target(
    mirror_root: Path,
    relative_path: Path,
    source_label: str,
) -> Path:
    if (
        relative_path.is_absolute()
        or relative_path.anchor
        or any(part in {".", ".."} for part in relative_path.parts)
    ):
        raise SecretScanError("SECRET_SCAN_INPUT_INVALID", f"{source_label} has an unsafe path.")
    for component in relative_path.parts:
        _validate_windows_component(component, source_label)
    return mirror_root / relative_path


def _write_mirror_input(
    target: Path,
    content: bytes,
    source_label: str,
    budget: _SecretScanEnumerationBudget,
) -> None:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = RAW_SCAN_SENTINEL + content
        budget.consume_work(len(payload))
        with target.open("xb") as stream:
            written = stream.write(payload)
        if written != len(payload):
            raise OSError("short scanner-mirror write")
    except OSError as error:
        raise SecretScanError(
            "SECRET_SCAN_INPUT_INVALID",
            f"{source_label} could not be prepared for path-aware scanning.",
        ) from error


def _path_rule_alias(relative_path: Path) -> Path | None:
    """Return a safe alias for every path rule in pinned Gitleaks 8.30.1."""

    folded_name = relative_path.name.casefold()
    if folded_name.endswith("nuget.config"):
        return Path("pws-rule.nuget.config")
    for suffix in PINNED_PATH_RULE_SUFFIXES:
        if folded_name.endswith(suffix):
            return Path(f"pws-rule{suffix}")
    return None


def _scan_mirror(
    gitleaks: str,
    trusted_ignore: Path,
    mirror_root: Path,
    target: Path,
    source_label: str,
    budget: _SecretScanEnumerationBudget,
) -> None:
    completed = _capture_bytes(
        (
            gitleaks,
            "dir",
            "--gitleaks-ignore-path",
            str(trusted_ignore),
            "--config",
            str(_trusted_gitleaks_config(trusted_ignore)),
            "--no-banner",
            "--no-color",
            "--ignore-gitleaks-allow",
            "--redact=100",
            "--exit-code",
            "42",
            "--timeout",
            "120",
            str(target),
        ),
        cwd=mirror_root,
        stdout_limit=MAX_SCANNER_DIAGNOSTIC_BYTES,
        timeout_seconds=budget.subprocess_timeout(SCANNER_SUBPROCESS_TIMEOUT_SECONDS),
    )
    if completed.returncode == 42:
        raise SecretScanError(
            "SECRET_SCAN_FINDING",
            f"{source_label} contains a potential secret.",
            exit_code=42,
        )
    if completed.returncode != 0:
        raise SecretScanError("SECRET_SCAN_TOOL_FAILED", f"Scanner failed for {source_label}.")


def _scan_path_aware_blobs(
    gitleaks: str,
    git: str,
    repository_root: Path,
    trusted_ignore: Path,
    entries: Sequence[_PathBlobEntry],
    *,
    category: str,
    budget: _SecretScanEnumerationBudget,
) -> None:
    batches: list[list[tuple[int, _PathBlobEntry]]] = []
    batch_sizes: list[int] = []
    for entry_number, entry in enumerate(entries, start=1):
        item_size = len(RAW_SCAN_SENTINEL) + entry.object_size
        if entry.object_size > MAX_SECRET_SCAN_BYTES:
            raise SecretScanError(
                "SECRET_SCAN_INPUT_TOO_LARGE",
                f"{category} item {entry_number} exceeds the 50 MiB scanner limit.",
            )
        if batches and batch_sizes[-1] + item_size <= MAX_SECRET_SCAN_BATCH_BYTES:
            batches[-1].append((entry_number, entry))
            batch_sizes[-1] += item_size
        else:
            batches.append([(entry_number, entry)])
            batch_sizes.append(item_size)

    for batch_number, batch in enumerate(batches, start=1):
        with tempfile.TemporaryDirectory(prefix="pws-secret-blob-path-") as temporary:
            temporary_root = Path(temporary).resolve()
            mirror_root = temporary_root / "source"
            shadow_root = temporary_root / "path-rule-shadow"
            shadow_written = False
            for entry_number, entry in batch:
                source_label = f"{category} item {batch_number}.{entry_number}"
                mirror_target = _safe_mirror_target(
                    mirror_root,
                    Path(f"item-{entry_number:08d}") / entry.relative_path,
                    source_label,
                )
                content = _read_git_blob(
                    git,
                    repository_root,
                    entry.object_id,
                    entry.object_size,
                    source_label,
                    budget,
                )
                _write_mirror_input(mirror_target, content, source_label, budget)
                rule_alias = _path_rule_alias(entry.relative_path)
                if rule_alias is not None:
                    shadow_target = _safe_mirror_target(
                        shadow_root,
                        Path(f"item-{entry_number:08d}") / rule_alias,
                        source_label,
                    )
                    _write_mirror_input(shadow_target, content, source_label, budget)
                    shadow_written = True
            _scan_mirror(
                gitleaks,
                trusted_ignore,
                mirror_root,
                Path("."),
                f"{category} batch {batch_number}",
                budget,
            )
            if shadow_written:
                _scan_mirror(
                    gitleaks,
                    trusted_ignore,
                    shadow_root,
                    Path("."),
                    f"{category} shadow batch {batch_number}",
                    budget,
                )


def _scan_working_path_batch(
    gitleaks: str,
    trusted_ignore: Path,
    items: Sequence[tuple[Path, bytes, str]],
    *,
    category: str,
    batch_number: int,
    budget: _SecretScanEnumerationBudget,
) -> None:
    if not items:
        return
    with tempfile.TemporaryDirectory(prefix="pws-secret-work-path-") as temporary:
        temporary_root = Path(temporary).resolve()
        mirror_root = temporary_root / "source"
        shadow_root = temporary_root / "path-rule-shadow"
        shadow_written = False
        for item_number, (relative_path, content, source_label) in enumerate(items, start=1):
            mirror_target = _safe_mirror_target(mirror_root, relative_path, source_label)
            _write_mirror_input(mirror_target, content, source_label, budget)
            rule_alias = _path_rule_alias(relative_path)
            if rule_alias is not None:
                shadow_target = _safe_mirror_target(
                    shadow_root,
                    Path(f"item-{item_number:08d}") / rule_alias,
                    source_label,
                )
                _write_mirror_input(shadow_target, content, source_label, budget)
                shadow_written = True
        _scan_mirror(
            gitleaks,
            trusted_ignore,
            mirror_root,
            Path("."),
            f"{category} path batch {batch_number}",
            budget,
        )
        if shadow_written:
            _scan_mirror(
                gitleaks,
                trusted_ignore,
                shadow_root,
                Path("."),
                f"{category} shadow batch {batch_number}",
                budget,
            )


def _scan_path_bytes(
    paths: Sequence[Path],
    *,
    category: str,
    batcher: _SecretScanBatcher,
) -> None:
    for index, relative_path in enumerate(paths, start=1):
        batcher.add(f"{category} path {index}", os.fsencode(relative_path.as_posix()))


def _working_path_state(
    repository_root: Path,
    paths: Sequence[Path],
    *,
    category: str,
    allow_missing: bool,
    budget: _SecretScanEnumerationBudget,
) -> tuple[tuple[Path, bytes | None], ...]:
    state: list[tuple[Path, bytes | None]] = []
    for index, relative_path in enumerate(paths, start=1):
        content = _read_scan_input(
            repository_root,
            relative_path,
            allow_missing=allow_missing,
            source_label=f"{category} item {index}",
            work_budget=budget,
        )
        digest = None if content is None else hashlib.sha256(content).digest()
        state.append((relative_path, digest))
    return tuple(state)


def _prepare_trusted_gitleaks_config(
    repository_root: Path,
    tracked_paths: Sequence[Path],
    tracked_state: Sequence[tuple[Path, bytes | None]],
    trusted_ignore: Path,
    budget: _SecretScanEnumerationBudget,
) -> bytes:
    config_path = Path(".gitleaks.toml")
    try:
        config_index = tracked_paths.index(config_path)
    except ValueError as error:
        raise SecretScanError(
            "SECRET_SCAN_POLICY_INVALID",
            "the tracked Gitleaks configuration is required.",
        ) from error
    expected_path, expected_digest = tracked_state[config_index]
    if expected_path != config_path or expected_digest is None:
        raise SecretScanError(
            "SECRET_SCAN_POLICY_INVALID",
            "the tracked Gitleaks configuration is unavailable.",
        )
    content = _read_scan_input(
        repository_root,
        config_path,
        allow_missing=False,
        source_label="secret-scanning configuration",
        work_budget=budget,
    )
    if content is None or hashlib.sha256(content).digest() != expected_digest:
        raise SecretScanError(
            "SECRET_SCAN_INPUT_INVALID", "repository state changed during scanning."
        )
    trusted_config = _trusted_gitleaks_config(trusted_ignore)
    try:
        budget.consume_work(len(content))
        with trusted_config.open("xb") as stream:
            written = stream.write(content)
        if written != len(content):
            raise OSError("short trusted-config write")
    except OSError as error:
        raise SecretScanError(
            "SECRET_SCAN_INPUT_INVALID",
            "the secret-scanning configuration could not be isolated.",
        ) from error
    return expected_digest


def _scan_working_paths(
    gitleaks: str,
    repository_root: Path,
    trusted_ignore: Path,
    paths: Sequence[Path],
    *,
    category: str,
    allow_missing: bool,
    batcher: _SecretScanBatcher,
    expected_state: Sequence[tuple[Path, bytes | None]],
    budget: _SecretScanEnumerationBudget,
) -> int:
    if len(paths) != len(expected_state):
        raise SecretScanError(
            "SECRET_SCAN_INPUT_INVALID", "repository state changed during scanning."
        )
    scanned = 0
    batch_number = 1
    batch_size = 0
    batch_items: list[tuple[Path, bytes, str]] = []
    for index, (relative_path, expected) in enumerate(
        zip(paths, expected_state, strict=True),
        start=1,
    ):
        expected_path, expected_digest = expected
        if relative_path != expected_path:
            raise SecretScanError(
                "SECRET_SCAN_INPUT_INVALID", "repository state changed during scanning."
            )
        source_label = f"{category} item {index}"
        batcher.add(f"{category} path {index}", os.fsencode(relative_path.as_posix()))
        content = _read_scan_input(
            repository_root,
            relative_path,
            allow_missing=allow_missing,
            source_label=source_label,
            work_budget=budget,
        )
        actual_digest = None if content is None else hashlib.sha256(content).digest()
        if actual_digest != expected_digest:
            raise SecretScanError(
                "SECRET_SCAN_INPUT_INVALID", "repository state changed during scanning."
            )
        if content is None:
            continue
        batcher.add(source_label, content)
        item_size = len(RAW_SCAN_SENTINEL) + len(content)
        if batch_items and batch_size + item_size > MAX_SECRET_SCAN_BATCH_BYTES:
            _scan_working_path_batch(
                gitleaks,
                trusted_ignore,
                batch_items,
                category=category,
                batch_number=batch_number,
                budget=budget,
            )
            batch_number += 1
            batch_size = 0
            batch_items = []
        batch_items.append((relative_path, content, source_label))
        batch_size += item_size
        scanned += 1
    _scan_working_path_batch(
        gitleaks,
        trusted_ignore,
        batch_items,
        category=category,
        batch_number=batch_number,
        budget=budget,
    )
    return scanned


def _git_header_entries(header: bytes, source_label: str) -> list[list[bytes]]:
    entries: list[list[bytes]] = []
    for line in header.split(b"\n"):
        if line.startswith(b" "):
            if not entries:
                raise SecretScanError(
                    "SECRET_SCAN_GIT_FAILED", f"Git returned a malformed {source_label}."
                )
            entries[-1].append(line)
        else:
            entries.append([line])
    return entries


def _inspect_git_message_content(
    object_content: bytes,
    object_type: str,
    source_label: str,
    inspector: Callable[[bytes], None],
) -> None:
    header, separator, message_body = object_content.partition(b"\n\n")
    if not separator:
        raise SecretScanError("SECRET_SCAN_GIT_FAILED", f"Git could not read {source_label}.")
    identity_prefixes = (b"author ", b"committer ") if object_type == "commit" else (b"tagger ",)
    entries = _git_header_entries(header, f"{object_type} header")
    if any(
        sum(entry[0].startswith(prefix) for entry in entries) > 1 for prefix in identity_prefixes
    ):
        # A malformed duplicate identity header must not turn the provenance exception into a
        # hiding place. Inspect every header byte first so the policy callback can report any
        # prohibited content, then reject even a duplicate that otherwise looks harmless.
        inspector(header)
        raise SecretScanError(
            "SECRET_SCAN_GIT_FAILED", f"Git returned a malformed {object_type} header."
        )

    inspectable_entries: list[bytes] = []
    for entry in entries:
        first_line = entry[0]
        if first_line.startswith(identity_prefixes):
            inspectable_entries.extend(entry[1:])
        elif object_type == "commit" and first_line.startswith(b"mergetag "):
            embedded_tag = b"\n".join(
                (first_line.removeprefix(b"mergetag "), *(line[1:] for line in entry[1:]))
            )
            _inspect_git_message_content(
                embedded_tag,
                "tag",
                f"{source_label} mergetag",
                inspector,
            )
        else:
            inspectable_entries.extend(entry)
    inspector(b"\n".join(inspectable_entries))
    inspector(message_body)


def inspect_repository_contents(
    root: Path,
    inspector: Callable[[bytes], None],
) -> RepositoryContentSummary:
    """Inspect one complete, bounded Git/index/working snapshot with a pure callback.

    This reuses the secret scanner's hardened enumeration, path, object, completeness,
    replacement-object, and mutation controls without invoking Gitleaks. The callback must
    not retain or disclose source bytes and may raise its own sanitized policy exception.
    """

    budget = _SecretScanEnumerationBudget()
    try:
        repository_root = root.resolve(strict=True)
    except OSError as error:
        raise SecretScanError(
            "SECRET_SCAN_INPUT_INVALID", "repository root is not a readable directory."
        ) from error
    if not repository_root.is_dir():
        raise SecretScanError("SECRET_SCAN_INPUT_INVALID", "repository root is not a directory.")
    git = _git_executable(budget)
    _require_git_worktree_root(git, repository_root, budget)
    _require_local_ref_storage(git, repository_root, budget)
    _require_complete_git_history(git, repository_root, budget)
    history_roots = _history_roots(git, repository_root, budget)
    historical_path_entries = _historical_path_entries(
        git,
        repository_root,
        history_roots,
        budget,
    )
    index_entries = _index_entries(git, repository_root, budget)
    historical_blobs, historical_message_objects = _historical_objects(
        git,
        repository_root,
        history_roots,
        historical_path_entries,
        budget,
    )
    index_blobs = sorted({(entry.object_id, entry.object_size) for entry in index_entries})
    bounded_sizes = (
        *(object_size for _, object_size in historical_blobs),
        *(object_size for _, _, object_size in historical_message_objects),
        *(object_size for _, object_size in index_blobs),
    )
    if any(object_size > MAX_SECRET_SCAN_BYTES for object_size in bounded_sizes):
        raise SecretScanError(
            "SECRET_SCAN_INPUT_TOO_LARGE", "a Git object exceeds the 50 MiB scanner limit."
        )

    tracked_paths = _git_paths(git, repository_root, ("ls-files", "--cached"), budget)
    untracked_paths = _git_paths(
        git,
        repository_root,
        ("ls-files", "--others", "--exclude-standard"),
        budget,
    )
    item_count = sum(
        (
            len(history_roots.ref_names),
            len(historical_path_entries),
            len(index_entries),
            len(historical_blobs),
            len(historical_message_objects),
            len(tracked_paths),
            len(untracked_paths),
        )
    )
    if item_count > MAX_SECRET_SCAN_ITEMS:
        raise SecretScanError(
            "SECRET_SCAN_INPUT_TOO_LARGE",
            f"repository enumeration exceeds the {MAX_SECRET_SCAN_ITEMS:,}-item scanner limit.",
        )
    tracked_state = _working_path_state(
        repository_root,
        tracked_paths,
        category="tracked working-tree",
        allow_missing=True,
        budget=budget,
    )
    untracked_state = _working_path_state(
        repository_root,
        untracked_paths,
        category="untracked source",
        allow_missing=False,
        budget=budget,
    )

    for ref_name in history_roots.ref_names:
        budget.checkpoint()
        inspector(ref_name)
    for entry in historical_path_entries:
        budget.checkpoint()
        inspector(os.fsencode(entry.relative_path.as_posix()))
    for entry in index_entries:
        budget.checkpoint()
        inspector(os.fsencode(entry.relative_path.as_posix()))
    for item_number, (object_id, object_size) in enumerate(historical_blobs, start=1):
        inspector(
            _read_git_blob(
                git,
                repository_root,
                object_id,
                object_size,
                f"historical blob item {item_number}",
                budget,
            )
        )
    for item_number, (object_id, object_type, object_size) in enumerate(
        historical_message_objects,
        start=1,
    ):
        source_label = f"historical commit/tag object {item_number}"
        object_content = _read_git_object(
            git,
            repository_root,
            object_id,
            object_type,
            object_size,
            source_label,
            budget,
        )
        # Standard identity lines, including a folded merge-tag identity, are Git provenance.
        # Custom headers, continuations, and message bodies remain untrusted. Gitleaks separately
        # scans every complete raw object, including the provenance lines omitted by this callback.
        _inspect_git_message_content(object_content, object_type, source_label, inspector)
    for item_number, (object_id, object_size) in enumerate(index_blobs, start=1):
        inspector(
            _read_git_blob(
                git,
                repository_root,
                object_id,
                object_size,
                f"index blob item {item_number}",
                budget,
            )
        )

    def inspect_working_paths(
        paths: Sequence[Path],
        expected_state: Sequence[tuple[Path, bytes | None]],
        *,
        category: str,
        allow_missing: bool,
    ) -> int:
        if len(paths) != len(expected_state):
            raise SecretScanError(
                "SECRET_SCAN_INPUT_INVALID", "repository state changed during scanning."
            )
        inspected = 0
        for item_number, (relative_path, expected) in enumerate(
            zip(paths, expected_state, strict=True),
            start=1,
        ):
            expected_path, expected_digest = expected
            if relative_path != expected_path:
                raise SecretScanError(
                    "SECRET_SCAN_INPUT_INVALID", "repository state changed during scanning."
                )
            content = _read_scan_input(
                repository_root,
                relative_path,
                allow_missing=allow_missing,
                source_label=f"{category} item {item_number}",
                work_budget=budget,
            )
            actual_digest = None if content is None else hashlib.sha256(content).digest()
            if actual_digest != expected_digest:
                raise SecretScanError(
                    "SECRET_SCAN_INPUT_INVALID", "repository state changed during scanning."
                )
            inspector(os.fsencode(relative_path.as_posix()))
            if content is not None:
                inspector(content)
                inspected += 1
        return inspected

    tracked_count = inspect_working_paths(
        tracked_paths,
        tracked_state,
        category="tracked working-tree",
        allow_missing=True,
    )
    untracked_count = inspect_working_paths(
        untracked_paths,
        untracked_state,
        category="untracked source",
        allow_missing=False,
    )

    final_index_entries = _index_entries(git, repository_root, budget)
    final_tracked_paths = _git_paths(
        git,
        repository_root,
        ("ls-files", "--cached"),
        budget,
    )
    final_untracked_paths = _git_paths(
        git,
        repository_root,
        ("ls-files", "--others", "--exclude-standard"),
        budget,
    )
    if (
        final_index_entries != index_entries
        or final_tracked_paths != tracked_paths
        or final_untracked_paths != untracked_paths
        or _working_path_state(
            repository_root,
            final_tracked_paths,
            category="tracked working-tree",
            allow_missing=True,
            budget=budget,
        )
        != tracked_state
        or _working_path_state(
            repository_root,
            final_untracked_paths,
            category="untracked source",
            allow_missing=False,
            budget=budget,
        )
        != untracked_state
    ):
        raise SecretScanError(
            "SECRET_SCAN_INPUT_INVALID", "repository state changed during scanning."
        )
    _require_git_worktree_root(git, repository_root, budget)
    _require_local_ref_storage(git, repository_root, budget)
    _require_complete_git_history(git, repository_root, budget)
    if _history_roots(git, repository_root, budget) != history_roots:
        raise SecretScanError(
            "SECRET_SCAN_INPUT_INVALID", "Git refs changed during repository scanning."
        )
    budget.checkpoint()
    return RepositoryContentSummary(
        history_blob_count=len(historical_blobs),
        history_message_object_count=len(historical_message_objects),
        historical_path_count=len(historical_path_entries),
        index_blob_count=len(index_blobs),
        tracked_count=tracked_count,
        untracked_count=untracked_count,
    )


def _scan_repository_checked(
    root: Path,
    trusted_ignore: Path,
    enumeration_budget: _SecretScanEnumerationBudget,
) -> _SecretScanSummary:
    git = _git_executable(enumeration_budget)
    _require_git_worktree_root(git, root, enumeration_budget)
    _require_local_ref_storage(git, root, enumeration_budget)
    _require_complete_git_history(git, root, enumeration_budget)
    history_roots = _history_roots(git, root, enumeration_budget)
    historical_path_entries = _historical_path_entries(
        git,
        root,
        history_roots,
        enumeration_budget,
    )
    index_entries = _index_entries(git, root, enumeration_budget)
    historical_blobs, historical_message_objects = _historical_objects(
        git,
        root,
        history_roots,
        historical_path_entries,
        enumeration_budget,
    )
    bounded_sizes = (
        *(entry.object_size for entry in historical_path_entries),
        *(entry.object_size for entry in index_entries),
        *(object_size for _, object_size in historical_blobs),
        *(object_size for _, _, object_size in historical_message_objects),
    )
    if any(object_size > MAX_SECRET_SCAN_BYTES for object_size in bounded_sizes):
        raise SecretScanError(
            "SECRET_SCAN_INPUT_TOO_LARGE",
            "a Git object exceeds the 50 MiB scanner limit.",
        )
    index_blobs = sorted({(entry.object_id, entry.object_size) for entry in index_entries})
    raw_item_overhead = len(RAW_SCAN_SENTINEL) + 1
    anticipated_source_byte_work = (
        sum(raw_item_overhead + len(ref_name) for ref_name in history_roots.ref_names)
        + sum(
            raw_item_overhead + len(os.fsencode(entry.relative_path.as_posix()))
            for entry in historical_path_entries
        )
        + sum(
            raw_item_overhead + len(os.fsencode(entry.relative_path.as_posix()))
            for entry in index_entries
        )
    )
    anticipated_git_work = (
        anticipated_source_byte_work
        + sum(
            (entry.object_size + len(RAW_SCAN_SENTINEL))
            * (6 if _path_rule_alias(entry.relative_path) is not None else 4)
            for entry in (*historical_path_entries, *index_entries)
        )
        + 2
        * sum(
            (
                *(object_size + raw_item_overhead for _, object_size in historical_blobs),
                *(
                    object_size + raw_item_overhead
                    for _, _, object_size in historical_message_objects
                ),
                *(object_size + raw_item_overhead for _, object_size in index_blobs),
            )
        )
    )
    enumeration_budget.ensure_work_capacity(anticipated_git_work)

    tracked_paths = _git_paths(git, root, ("ls-files", "--cached"), enumeration_budget)
    untracked_paths = _git_paths(
        git,
        root,
        ("ls-files", "--others", "--exclude-standard"),
        enumeration_budget,
    )
    anticipated_current_path_work = sum(
        raw_item_overhead + len(os.fsencode(relative_path.as_posix()))
        for relative_path in (*tracked_paths, *untracked_paths)
    )
    enumeration_budget.ensure_work_capacity(anticipated_git_work + anticipated_current_path_work)
    scan_item_count = sum(
        (
            len(history_roots.ref_names),
            len(historical_path_entries),
            len(index_entries),
            len(historical_blobs),
            len(historical_message_objects),
            len(tracked_paths),
            len(untracked_paths),
        )
    )
    if scan_item_count > MAX_SECRET_SCAN_ITEMS:
        raise SecretScanError(
            "SECRET_SCAN_INPUT_TOO_LARGE",
            f"repository enumeration exceeds the {MAX_SECRET_SCAN_ITEMS:,}-item scanner limit.",
        )
    tracked_state = _working_path_state(
        root,
        tracked_paths,
        category="tracked working-tree",
        allow_missing=True,
        budget=enumeration_budget,
    )
    untracked_state = _working_path_state(
        root,
        untracked_paths,
        category="untracked source",
        allow_missing=False,
        budget=enumeration_budget,
    )
    trusted_config_digest = _prepare_trusted_gitleaks_config(
        root,
        tracked_paths,
        tracked_state,
        trusted_ignore,
        enumeration_budget,
    )
    enumeration_budget.ensure_work_capacity(anticipated_git_work + anticipated_current_path_work)
    gitleaks = _gitleaks_executable(enumeration_budget)
    batcher = _SecretScanBatcher(gitleaks, root, trusted_ignore, enumeration_budget)
    for index, ref_name in enumerate(history_roots.ref_names, start=1):
        batcher.add(f"Git ref name {index}", ref_name)
    _scan_path_bytes(
        [entry.relative_path for entry in historical_path_entries],
        category="historical source",
        batcher=batcher,
    )
    _scan_path_bytes(
        [entry.relative_path for entry in index_entries],
        category="index source",
        batcher=batcher,
    )
    _scan_path_aware_blobs(
        gitleaks,
        git,
        root,
        trusted_ignore,
        historical_path_entries,
        category="historical path/blob",
        budget=enumeration_budget,
    )
    _scan_path_aware_blobs(
        gitleaks,
        git,
        root,
        trusted_ignore,
        index_entries,
        category="index path/blob",
        budget=enumeration_budget,
    )

    history_blob_count = _scan_git_blobs(
        git,
        root,
        historical_blobs,
        category="historical blob",
        batcher=batcher,
        budget=enumeration_budget,
    )
    history_message_object_count = _scan_git_message_objects(
        git,
        root,
        historical_message_objects,
        batcher=batcher,
        budget=enumeration_budget,
    )
    index_count = _scan_git_blobs(
        git,
        root,
        index_blobs,
        category="index blob",
        batcher=batcher,
        budget=enumeration_budget,
    )

    tracked_count = _scan_working_paths(
        gitleaks,
        root,
        trusted_ignore,
        tracked_paths,
        category="tracked working-tree",
        allow_missing=True,
        batcher=batcher,
        expected_state=tracked_state,
        budget=enumeration_budget,
    )
    untracked_count = _scan_working_paths(
        gitleaks,
        root,
        trusted_ignore,
        untracked_paths,
        category="untracked source",
        allow_missing=False,
        batcher=batcher,
        expected_state=untracked_state,
        budget=enumeration_budget,
    )
    batcher.flush()
    final_index_entries = _index_entries(git, root, enumeration_budget)
    final_tracked_paths = _git_paths(
        git,
        root,
        ("ls-files", "--cached"),
        enumeration_budget,
    )
    final_untracked_paths = _git_paths(
        git,
        root,
        ("ls-files", "--others", "--exclude-standard"),
        enumeration_budget,
    )
    if (
        final_index_entries != index_entries
        or final_tracked_paths != tracked_paths
        or final_untracked_paths != untracked_paths
        or _working_path_state(
            root,
            final_tracked_paths,
            category="tracked working-tree",
            allow_missing=True,
            budget=enumeration_budget,
        )
        != tracked_state
        or _working_path_state(
            root,
            final_untracked_paths,
            category="untracked source",
            allow_missing=False,
            budget=enumeration_budget,
        )
        != untracked_state
    ):
        raise SecretScanError(
            "SECRET_SCAN_INPUT_INVALID", "repository state changed during scanning."
        )
    _require_git_worktree_root(git, root, enumeration_budget)
    _require_local_ref_storage(git, root, enumeration_budget)
    _require_complete_git_history(git, root, enumeration_budget)
    if _history_roots(git, root, enumeration_budget) != history_roots:
        raise SecretScanError(
            "SECRET_SCAN_INPUT_INVALID", "Git refs changed during repository scanning."
        )
    final_trusted_config = _read_scan_input(
        trusted_ignore.parent,
        Path(TRUSTED_GITLEAKS_CONFIG_NAME),
        allow_missing=False,
        source_label="isolated secret-scanning configuration",
        work_budget=enumeration_budget,
    )
    if (
        final_trusted_config is None
        or hashlib.sha256(final_trusted_config).digest() != trusted_config_digest
    ):
        raise SecretScanError(
            "SECRET_SCAN_INPUT_INVALID",
            "the isolated secret-scanning configuration changed during scanning.",
        )
    enumeration_budget.checkpoint()
    return _SecretScanSummary(
        history_blob_count=history_blob_count,
        history_message_object_count=history_message_object_count,
        historical_path_count=len(historical_path_entries),
        index_count=index_count,
        tracked_count=tracked_count,
        untracked_count=untracked_count,
    )


def scan_repository(root: Path = PROJECT_ROOT) -> int:
    """Scan reachable Git objects/paths plus exact index and nonignored working bytes."""

    try:
        scan_budget = _SecretScanEnumerationBudget()
        root = root.resolve(strict=True)
        if not root.is_dir():
            raise SecretScanError(
                "SECRET_SCAN_INPUT_INVALID", "repository root is not a directory."
            )
        with tempfile.TemporaryDirectory(prefix="pws-secret-control-") as temporary:
            trusted_ignore = Path(temporary).resolve() / "trusted-empty-ignore"
            with trusted_ignore.open("xb"):
                pass
            summary = _scan_repository_checked(root, trusted_ignore, scan_budget)
    except SecretScanError as error:
        print(f"ERROR [{error.code}] {error.message}", file=sys.stderr)
        return error.exit_code
    except OSError:
        print(
            "ERROR [SECRET_SCAN_TOOL_FAILED] repository scanning failed.",
            file=sys.stderr,
        )
        return 1

    print(
        "OK: secret scan covered reachable Git object and source-path bytes, "
        f"{summary.history_blob_count} historical blobs, "
        f"{summary.history_message_object_count} historical commit/tag objects, "
        f"{summary.historical_path_count} historical path/blob pairs, "
        f"{summary.index_count} index blobs, "
        f"{summary.tracked_count} tracked working-tree files, and "
        f"{summary.untracked_count} "
        "nonignored untracked files."
    )
    return 0


def _list_commands() -> int:
    print("Stable developer commands (tools/dev.py is the source of truth):")
    for name, command in COMMANDS.items():
        quality_marker = " [check]" if command.include_in_check else ""
        print(f"  {name:<28} {command.description}{quality_marker}")
    print("  bootstrap                    Synchronize the locked development environment.")
    print("  check                        Run every command marked [check], in order.")
    return 0


def run_named(command: str) -> int:
    """Run one named command and return its process exit code."""

    if command == "bootstrap":
        return _run((_required_executable("uv"), "sync", "--locked"))
    if command == "list":
        return _list_commands()
    if command == "check":
        for name in CHECK_COMMANDS:
            result = run_named(name)
            if result != 0:
                return result
        return 0

    specification = COMMANDS[command]
    if specification.mode is CommandMode.GITLEAKS:
        return scan_repository()
    return _run(
        (
            _required_executable("uv"),
            "run",
            "--locked",
            "--no-sync",
            *specification.arguments,
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Parse command-line arguments and run the selected developer command."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=sorted((*COMMANDS, "bootstrap", "check", "list")),
        help="developer command to run",
    )
    arguments = parser.parse_args(argv)
    try:
        return run_named(arguments.command)
    except ToolRequirementError as error:
        missing_or_mismatched = str(error)
        if missing_or_mismatched == "uv":
            message = "uv 0.11.32 is required; see README.md for installation instructions."
        elif missing_or_mismatched == "gitleaks":
            message = (
                f"gitleaks {GITLEAKS_VERSION} is required; install it with "
                f"'winget install --id Gitleaks.Gitleaks --version {GITLEAKS_VERSION} -e'."
            )
        elif missing_or_mismatched == "git":
            minimum = ".".join(str(part) for part in MINIMUM_GIT_VERSION)
            message = f"Git {minimum} or newer is required; the installed tool is unavailable or mismatched."
        else:
            message = missing_or_mismatched
        print(f"ERROR [TOOL_REQUIREMENT] {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

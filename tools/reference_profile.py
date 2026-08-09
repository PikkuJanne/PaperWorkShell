"""Validate and exercise the machine-readable reference environment profile."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import platform
import shutil
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, NoReturn, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
PROFILE_PATH: Final = Path("benchmarks/reference-windows-v1.json")
SCHEMA_PATH: Final = Path("schemas/reference-profile.schema.json")
SCENARIO_IDS: Final = frozenset({"local-smoke", "limited-storage", "rotational-hdd"})
GENERATOR_ID: Final = "sha256-seed-repeat-v1"


@dataclass(frozen=True, order=True)
class ProfileIssue:
    """One stable profile validation error."""

    code: str
    path: str
    message: str

    def render(self) -> str:
        return f"ERROR [{self.code}] {self.path}: {self.message}"


class BenchmarkError(Exception):
    """An expected benchmark failure with a stable code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details) if details is not None else None


class DuplicateJsonKeyError(ValueError):
    """Raised when a JSON object repeats a member name."""


class NonStandardJsonConstantError(ValueError):
    """Raised for NaN and Infinity values that are not standard JSON."""


@dataclass(frozen=True)
class ReferenceAttestations:
    """Operator-confirmed properties that portable APIs cannot prove."""

    memory_mib: int | None = None
    graphics_class: str | None = None
    storage_media: str | None = None


@dataclass(frozen=True)
class DiskUsage:
    """Portable subset of the platform disk-usage result."""

    total: int
    used: int
    free: int


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _json_path(parts: Sequence[object]) -> str:
    rendered = "$"
    for part in parts:
        rendered += f"[{part}]" if isinstance(part, int) else f".{part}"
    return rendered


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKeyError(key)
        result[key] = value
    return result


def _reject_json_constant(value: str) -> NoReturn:
    raise NonStandardJsonConstantError(value)


def _load_json_object(path: Path) -> tuple[dict[str, Any] | None, list[ProfileIssue]]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None, [
            ProfileIssue(
                "REFERENCE_TEXT_ENCODING_INVALID",
                path.name,
                "input must be valid UTF-8",
            )
        ]
    except OSError as error:
        return None, [ProfileIssue("REFERENCE_INPUT_UNREADABLE", path.name, type(error).__name__)]
    try:
        loaded = json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except DuplicateJsonKeyError:
        return None, [
            ProfileIssue(
                "REFERENCE_JSON_DUPLICATE_KEY",
                path.name,
                "object member names must be unique",
            )
        ]
    except NonStandardJsonConstantError:
        return None, [
            ProfileIssue(
                "REFERENCE_JSON_NONSTANDARD_CONSTANT",
                path.name,
                "NaN and Infinity are not permitted",
            )
        ]
    except json.JSONDecodeError as error:
        return None, [
            ProfileIssue(
                "REFERENCE_JSON_INVALID",
                path.name,
                f"line {error.lineno}: {error.msg}",
            )
        ]
    if not isinstance(loaded, dict):
        return None, [
            ProfileIssue("REFERENCE_JSON_ROOT_TYPE", path.name, "root value must be an object")
        ]
    return cast(dict[str, Any], loaded), []


def validate_profile(root: Path = PROJECT_ROOT) -> tuple[dict[str, Any] | None, list[ProfileIssue]]:
    """Load and validate the checked-in reference profile without network resolution."""

    profile, profile_issues = _load_json_object(root / PROFILE_PATH)
    schema, schema_issues = _load_json_object(root / SCHEMA_PATH)
    issues = [*profile_issues, *schema_issues]
    if issues or profile is None or schema is None:
        return None, sorted(issues)

    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        return None, [
            ProfileIssue(
                "REFERENCE_SCHEMA_DEFINITION_INVALID",
                _json_path(tuple(error.absolute_schema_path)),
                error.message,
            )
        ]

    validator = Draft202012Validator(schema)
    for validation_error in validator.iter_errors(profile):
        issues.append(
            ProfileIssue(
                "REFERENCE_PROFILE_INVALID",
                _json_path(tuple(validation_error.absolute_path)),
                validation_error.message,
            )
        )
    if issues:
        return None, sorted(issues)

    scenarios = cast(list[Mapping[str, Any]], profile["storage_scenarios"])
    scenario_map = {cast(str, scenario["id"]): scenario for scenario in scenarios}
    if len(scenario_map) != len(scenarios):
        issues.append(
            ProfileIssue(
                "REFERENCE_SCENARIO_ID_DUPLICATE",
                "$.storage_scenarios",
                "scenario IDs must be unique",
            )
        )
    if set(scenario_map) != SCENARIO_IDS:
        issues.append(
            ProfileIssue(
                "REFERENCE_SCENARIOS_INVALID",
                "$.storage_scenarios",
                "exactly local-smoke, limited-storage, and rotational-hdd are required",
            )
        )
    expected_scenarios: Final[dict[str, tuple[str, str, str, int | None, int | None]]] = {
        "local-smoke": ("observed", "observed", "runtime-observation", None, None),
        "limited-storage": (
            "bounded",
            "observed",
            "runtime-observation",
            8_589_934_592,
            4_194_304,
        ),
        "rotational-hdd": (
            "bounded",
            "hdd-or-validated-emulation",
            "runtime-and-operator-attested",
            8_589_934_592,
            4_194_304,
        ),
    }
    for scenario_id, expected in expected_scenarios.items():
        scenario = scenario_map.get(scenario_id)
        if scenario is None:
            continue
        actual = (
            cast(str, scenario["capacity"]),
            cast(str, scenario["media"]),
            cast(str, scenario["verification"]),
            cast(int | None, scenario.get("maximum_capacity_bytes")),
            cast(int | None, scenario.get("minimum_free_bytes")),
        )
        if actual != expected:
            issues.append(
                ProfileIssue(
                    "REFERENCE_SCENARIO_POLICY_INVALID",
                    f"$.storage_scenarios.{scenario_id}",
                    f"expected the pinned capacity/media/verification envelope {expected!r}",
                )
            )

    workload = cast(Mapping[str, Any], profile["workload"])
    bytes_per_file = cast(int, workload["bytes_per_file"])
    block_size = cast(int, workload["block_size_bytes"])
    if bytes_per_file % block_size != 0:
        issues.append(
            ProfileIssue(
                "REFERENCE_WORKLOAD_ALIGNMENT",
                "$.workload.block_size_bytes",
                "block size must divide bytes_per_file exactly",
            )
        )
    return (None, sorted(issues)) if issues else (profile, [])


def _total_memory_bytes() -> int | None:
    if sys.platform != "win32":
        return None
    status = _MemoryStatusEx()
    status.dwLength = ctypes.sizeof(_MemoryStatusEx)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    query = kernel32.GlobalMemoryStatusEx
    query.argtypes = [ctypes.POINTER(_MemoryStatusEx)]
    query.restype = ctypes.c_int
    if query(ctypes.byref(status)) == 0:
        return None
    return int(status.ullTotalPhys)


def _allocated_logical_processors() -> int | None:
    if "PYTHON_CPU_COUNT" in os.environ or "cpu_count" in sys._xoptions:
        raise BenchmarkError(
            "REFERENCE_CPU_OVERRIDE",
            "interpreter CPU-count overrides are not permitted for reference measurements",
        )
    if sys.platform != "win32":
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    current_process = kernel32.GetCurrentProcess
    current_process.argtypes = []
    current_process.restype = ctypes.c_void_p
    affinity = kernel32.GetProcessAffinityMask
    affinity.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.POINTER(ctypes.c_size_t),
    ]
    affinity.restype = ctypes.c_int
    process_mask = ctypes.c_size_t()
    system_mask = ctypes.c_size_t()
    if affinity(current_process(), ctypes.byref(process_mask), ctypes.byref(system_mask)) == 0:
        return None
    count = int(process_mask.value).bit_count()
    return count or None


def _deterministic_block(seed: str, block_size: int) -> bytes:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    repetitions = (block_size + len(digest) - 1) // len(digest)
    return (digest * repetitions)[:block_size]


def _write_generated_file(target: Path, block: bytes, block_count: int) -> tuple[int, str]:
    expected = hashlib.sha256()
    started = time.perf_counter_ns()
    with target.open("wb") as stream:
        for _ in range(block_count):
            stream.write(block)
            expected.update(block)
        stream.flush()
        os.fsync(stream.fileno())
    return time.perf_counter_ns() - started, expected.hexdigest()


def _read_generated_file(target: Path, block_size: int) -> tuple[int, str, int]:
    started = time.perf_counter_ns()
    digest = hashlib.sha256()
    bytes_read = 0
    with target.open("rb") as stream:
        while chunk := stream.read(block_size):
            digest.update(chunk)
            bytes_read += len(chunk)
    return bytes_read, digest.hexdigest(), time.perf_counter_ns() - started


def _disk_usage(work_directory: Path) -> DiskUsage:
    try:
        usage = shutil.disk_usage(work_directory)
    except OSError as error:
        raise BenchmarkError("BENCHMARK_IO_FAILED", type(error).__name__) from error
    return DiskUsage(total=usage.total, used=usage.used, free=usage.free)


def _run_generated_io(
    workload: Mapping[str, Any],
    work_directory: Path,
) -> tuple[list[dict[str, object]], int, int]:
    file_count = cast(int, workload["file_count"])
    bytes_per_file = cast(int, workload["bytes_per_file"])
    block_size = cast(int, workload["block_size_bytes"])
    seed = cast(str, workload["seed"])
    generator = cast(str, workload["generator"])
    pinned_digest = cast(str, workload["expected_file_sha256"])
    if generator != GENERATOR_ID:
        raise BenchmarkError(
            "BENCHMARK_WORKLOAD_MISMATCH",
            "the generated workload algorithm does not match the versioned profile",
        )
    required_bytes = file_count * bytes_per_file
    disk_before = _disk_usage(work_directory)
    if disk_before.free < required_bytes:
        raise BenchmarkError(
            "BENCHMARK_STORAGE_INSUFFICIENT",
            "the selected work directory does not have enough free space for the fixed workload",
        )

    block = _deterministic_block(seed, block_size)
    blocks_per_file = bytes_per_file // block_size
    measurements: list[dict[str, object]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="pws-reference-", dir=work_directory) as temporary:
            temporary_root = Path(temporary)
            for index in range(file_count):
                target = temporary_root / f"generated-{index}.bin"
                write_elapsed, expected_digest = _write_generated_file(
                    target, block, blocks_per_file
                )
                if expected_digest != pinned_digest:
                    raise BenchmarkError(
                        "BENCHMARK_WORKLOAD_MISMATCH",
                        "the generated workload checksum does not match the versioned profile",
                    )
                bytes_read, observed_digest, read_elapsed = _read_generated_file(target, block_size)
                if bytes_read != bytes_per_file:
                    raise BenchmarkError(
                        "BENCHMARK_IO_FAILED",
                        "generated workload byte count changed during the read-back check",
                    )
                if observed_digest != pinned_digest:
                    raise BenchmarkError(
                        "BENCHMARK_IO_FAILED",
                        "generated workload checksum changed during the read-back check",
                    )
                measurements.append(
                    {
                        "file_index": index,
                        "bytes_written": bytes_per_file,
                        "bytes_read": bytes_read,
                        "sha256": observed_digest,
                        "write_elapsed_ns": write_elapsed,
                        "read_elapsed_ns": read_elapsed,
                    }
                )
    except BenchmarkError:
        raise
    except OSError as error:
        raise BenchmarkError("BENCHMARK_IO_FAILED", type(error).__name__) from error

    disk_after = _disk_usage(work_directory)
    return measurements, disk_before.free, disk_after.free


def _normalize_architecture(value: str) -> str:
    return "x86_64" if value.casefold() in {"amd64", "x86_64"} else value.casefold()


def _observed_environment(work_directory: Path) -> dict[str, object]:
    disk = _disk_usage(work_directory)
    return {
        "os_family": platform.system(),
        "architecture": _normalize_architecture(platform.machine()),
        "logical_processors": _allocated_logical_processors(),
        "memory_bytes": _total_memory_bytes(),
        "storage_capacity_bytes": disk.total,
        "storage_free_bytes": disk.free,
        "graphics": None,
        "storage_media": None,
    }


def _requirement(
    required: object,
    actual: object,
    verification: str,
    *,
    actual_field: str,
) -> dict[str, object]:
    if actual is None:
        status = "unknown"
    elif actual == required:
        status = "matched"
    else:
        status = "mismatched"
    return {
        "required": required,
        actual_field: actual,
        "verification": verification,
        "status": status,
    }


def _maximum_requirement(maximum: int, actual: object) -> dict[str, object]:
    if actual is None:
        status = "unknown"
    elif isinstance(actual, int) and actual <= maximum:
        status = "matched"
    else:
        status = "mismatched"
    return {
        "required_maximum": maximum,
        "observed": actual,
        "verification": "runtime-observation",
        "status": status,
    }


def _minimum_requirement(minimum: int, actual: object) -> dict[str, object]:
    if actual is None:
        status = "unknown"
    elif isinstance(actual, int) and actual >= minimum:
        status = "matched"
    else:
        status = "mismatched"
    return {
        "required_minimum": minimum,
        "observed": actual,
        "verification": "runtime-observation",
        "status": status,
    }


def _evaluate_reference_environment(
    profile: Mapping[str, Any],
    scenario_id: str,
    observations: Mapping[str, object],
    attestations: ReferenceAttestations,
) -> dict[str, object]:
    if scenario_id == "local-smoke":
        return {"status": "not-evaluated", "requirements": {}}

    environment = cast(Mapping[str, Any], profile["environment"])
    graphics = cast(Mapping[str, Any], environment["graphics"])
    scenarios = cast(list[Mapping[str, Any]], profile["storage_scenarios"])
    scenario = next(item for item in scenarios if item["id"] == scenario_id)
    requirements: dict[str, dict[str, object]] = {
        "os_family": _requirement(
            cast(str, environment["os_family"]),
            observations["os_family"],
            "runtime-observation",
            actual_field="observed",
        ),
        "architecture": _requirement(
            cast(str, environment["architecture"]),
            observations["architecture"],
            "runtime-observation",
            actual_field="observed",
        ),
        "logical_processors": _requirement(
            cast(int, environment["logical_processors"]),
            observations["logical_processors"],
            "runtime-observation",
            actual_field="observed",
        ),
        "memory_mib": _requirement(
            cast(int, environment["memory_mib"]),
            attestations.memory_mib,
            "operator-attested-allocation",
            actual_field="attested",
        ),
        "graphics_class": _requirement(
            cast(str, graphics["class"]),
            attestations.graphics_class,
            "operator-attested",
            actual_field="attested",
        ),
        "storage_capacity_bytes": _maximum_requirement(
            cast(int, scenario["maximum_capacity_bytes"]),
            observations["storage_capacity_bytes"],
        ),
        "storage_free_bytes": _minimum_requirement(
            cast(int, scenario["minimum_free_bytes"]),
            observations["storage_free_bytes"],
        ),
    }
    if scenario_id == "rotational-hdd":
        requirements["storage_media"] = _requirement(
            "hdd-or-validated-emulation",
            attestations.storage_media,
            "operator-attested",
            actual_field="attested",
        )
    statuses = {cast(str, requirement["status"]) for requirement in requirements.values()}
    status = "matched" if statuses == {"matched"} else "mismatched"
    return {"status": status, "requirements": requirements}


def benchmark_report(
    profile: Mapping[str, Any],
    scenario_id: str,
    work_directory: Path,
    *,
    attestations: ReferenceAttestations,
) -> dict[str, object]:
    """Run the fixed generated-I/O workload and return sanitized measurements."""

    if scenario_id not in SCENARIO_IDS:
        raise BenchmarkError("REFERENCE_SCENARIO_UNKNOWN", scenario_id)
    try:
        work_directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise BenchmarkError("BENCHMARK_IO_FAILED", type(error).__name__) from error
    if not work_directory.is_dir():
        raise BenchmarkError("BENCHMARK_IO_FAILED", "work directory is not a directory")

    observations = _observed_environment(work_directory)
    evaluation = _evaluate_reference_environment(profile, scenario_id, observations, attestations)
    if evaluation["status"] == "mismatched":
        raise BenchmarkError(
            "REFERENCE_ENVIRONMENT_MISMATCH",
            "the selected host, allocation, or storage attestations do not match the profile",
            details={"reference_environment_evaluation": evaluation},
        )

    workload = cast(Mapping[str, Any], profile["workload"])
    measurements, free_before, free_after = _run_generated_io(workload, work_directory)
    observations["storage_free_bytes_before"] = free_before
    observations["storage_free_bytes_after"] = free_after
    observations.pop("storage_free_bytes")
    return {
        "report_schema_version": 1,
        "profile_id": cast(str, profile["profile_id"]),
        "scenario_id": scenario_id,
        "measurement_status": "record-only",
        "reference_environment_evaluation": evaluation,
        "observed_environment": observations,
        "workload": {
            "id": cast(str, workload["id"]),
            "generator": cast(str, workload["generator"]),
            "data_class": cast(str, workload["data_class"]),
            "file_count": cast(int, workload["file_count"]),
            "bytes_per_file": cast(int, workload["bytes_per_file"]),
            "block_size_bytes": cast(int, workload["block_size_bytes"]),
            "expected_file_sha256": cast(str, workload["expected_file_sha256"]),
        },
        "measurements": measurements,
        "product_targets": [],
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Run profile validation or the record-only benchmark entry point."""

    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="validate the checked-in profile")
    benchmark = subparsers.add_parser("benchmark", help="run the generated-I/O workload")
    benchmark.add_argument("--scenario", choices=sorted(SCENARIO_IDS), default="local-smoke")
    benchmark.add_argument(
        "--work-directory",
        type=Path,
        default=PROJECT_ROOT / ".paperworkshell-local" / "benchmark-work",
    )
    benchmark.add_argument(
        "--attest-memory-mib",
        type=int,
        help="operator-attested assigned memory in MiB",
    )
    benchmark.add_argument(
        "--attest-graphics",
        choices=("integrated",),
        help="operator-attested graphics class",
    )
    benchmark.add_argument(
        "--attest-storage-media",
        choices=("hdd-or-validated-emulation",),
        help="operator-attested storage media class",
    )
    arguments = parser.parse_args(argv)

    profile, issues = validate_profile()
    if issues:
        for issue in issues:
            print(issue.render(), file=sys.stderr)
        return 1
    assert profile is not None
    if arguments.command == "validate":
        print(
            "OK: reference-windows-v1.json defines the 2-core, 4096-MiB, "
            "integrated-graphics, limited-storage, and HDD profiles."
        )
        return 0

    try:
        report = benchmark_report(
            profile,
            cast(str, arguments.scenario),
            cast(Path, arguments.work_directory),
            attestations=ReferenceAttestations(
                memory_mib=cast(int | None, arguments.attest_memory_mib),
                graphics_class=cast(str | None, arguments.attest_graphics),
                storage_media=cast(str | None, arguments.attest_storage_media),
            ),
        )
    except BenchmarkError as error:
        print(f"ERROR [{error.code}] {error.message}", file=sys.stderr)
        if error.details is not None:
            print(json.dumps(error.details, indent=2, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

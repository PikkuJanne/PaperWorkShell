"""Run and publish the canonical PWS-0114 live vertical-slice evidence.

This module is developer tooling, not a product document-ingest surface.  It
accepts no source-document, destination-name, or fixture arguments.  The one
supported workload is the checked-in PWS-0114 integration scenario.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import hmac
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Final, NoReturn, cast
from uuid import UUID, uuid4

from paperworkshell.__main__ import (
    _desktop_worker_config,
    build_windows_pdf_export_worker_config,
)
from paperworkshell.domain.manifest_repository import CURRENT_MANIFEST_SCHEMA_VERSION
from paperworkshell.domain.pdf_export import PDF_EXPORT_PROFILE_VERSION
from paperworkshell.domain.resource_policy import (
    CONSTRAINED_RESOURCE_POLICY_V1,
    RESOURCE_PROFILE_ENVIRONMENT_VARIABLE,
    ResourcePolicy,
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
from paperworkshell.worker.client._windows import (
    _ApprovedResourceInventory,
    _open_approved_resource_inventory,
    _require_retained_resource_unchanged,
)
from paperworkshell.worker.pdf_export_report import QPDF_EXPORT_ENGINE_VERSION
from paperworkshell.worker.pdf_thumbnail_profile import (
    PDF_THUMBNAIL_RENDERER_PROFILE_SHA256,
)
from paperworkshell.worker.protocol.models import PROTOCOL_VERSION
from tools.m1_vertical_slice_benchmark import (
    BudgetId,
    BudgetResult,
    CompletedRunEvidence,
    FailedRunEvidence,
    FailureStage,
    M1BenchmarkError,
    M1BenchmarkReport,
    M1Phase,
    PhaseMeasurement,
    PhaseOutcome,
    VerticalSliceRunStatus,
    WorkerOperation,
    WorkerResourceMeasurement,
    WorkspaceCheckpoint,
    WorkspaceUsageMeasurement,
    _ceil_milliseconds,
    _LiveM1ReportWriter,
    _open_live_m1_report_writer,
    build_m1_benchmark_report,
)

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
LIVE_PROTOCOL_VERSION: Final = 2
LIVE_TEST_NODE: Final = (
    "tests/integration/test_m1_vertical_slice_windows.py::"
    "test_m1_vertical_slice_is_deterministic_recoverable_and_private"
)
LIVE_REQUIRED_TEST_NODES: Final = (
    LIVE_TEST_NODE,
    "tests/integration/test_source_ingest_windows.py::"
    "test_disk_full_during_import_copy_removes_partial_stage_and_preserves_state",
    "tests/integration/test_thumbnail_cache_storage_windows.py::"
    "test_disk_full_during_thumbnail_write_cleans_partial_stage_and_restart_is_clean",
    "tests/integration/test_export_destination_windows.py::"
    "test_disk_full_after_prefix_removes_only_the_exact_stage",
    "tests/integration/test_manifest_repository_windows.py::"
    "test_slow_atomic_rename_keeps_prior_manifest_authoritative_until_commit",
    "tests/integration/test_organizer_ui_lifecycle.py::"
    "test_artificially_slow_storage_flush_does_not_block_the_gui_event_loop",
    "tests/integration/test_organizer_ui_lifecycle.py::"
    "test_slow_storage_import_keeps_progress_visible_and_cancellation_available",
    "tests/integration/test_organizer_ui_lifecycle.py::"
    "test_artificially_slow_storage_read_does_not_block_the_gui_event_loop",
    "tests/integration/test_organizer_ui_lifecycle.py::"
    "test_artificially_slow_directory_scan_does_not_block_the_gui_event_loop",
    "tests/integration/test_pdf_thumbnail_worker_windows.py::"
    "test_live_worker_termination_during_render_recovers_with_fresh_service",
    "tests/integration/test_worker_windows_containment.py::"
    "test_worker_client_real_launch_operation_cancel_restart_and_shutdown",
    "tests/unit/worker/test_private_temp.py::"
    "test_capacity_preflight_accepts_exact_boundary_and_rejects_one_byte_less",
    "tests/unit/worker/test_pdf_thumbnail_worker_adapter.py::"
    "test_mid_render_private_disk_full_cleans_partial_and_fresh_retry_recovers",
    "tests/unit/worker/test_pdf_export_worker_adapter.py::"
    "test_mid_export_private_disk_full_removes_partial_and_fresh_retry_recovers",
)
LIVE_OUTPUT_DIRECTORY: Final = PROJECT_ROOT / "benchmark-results"
LIVE_FIXTURE_DIRECTORY: Final = PROJECT_ROOT / "tests" / "fixtures" / "synthetic" / "v1" / "pdf"
LIVE_FIXTURES: Final = (
    LIVE_FIXTURE_DIRECTORY / "render-calibration.pdf",
    LIVE_FIXTURE_DIRECTORY / "born-digital.pdf",
)
LIVE_DERIVATIVE_FILENAME: Final = "PWS-0114-m1-vertical-slice.pdf"
LIVE_REPORT_FILENAME: Final = "PWS-0114-m1-benchmark-report.json"
LIVE_CHILD_RESULT_FILENAME: Final = "result.json"
LIVE_CHILD_DERIVATIVE_FILENAME: Final = "derivative.pdf"
LIVE_ENV_PROTOCOL: Final = "PWS_M1_LIVE_PROTOCOL"
LIVE_ENV_RUN_ID: Final = "PWS_M1_LIVE_RUN_ID"
LIVE_ENV_STAGE: Final = "PWS_M1_LIVE_STAGE"
LIVE_ENV_HMAC_KEY: Final = "PWS_M1_LIVE_HMAC_KEY"
LIVE_ENV_NAMES: Final = (
    LIVE_ENV_PROTOCOL,
    LIVE_ENV_RUN_ID,
    LIVE_ENV_STAGE,
    LIVE_ENV_HMAC_KEY,
)
MAX_CHILD_RESULT_BYTES: Final = 256 * 1024
MAX_DERIVATIVE_BYTES: Final = 256 * 1024 * 1024
LIVE_RUN_TIMEOUT_SECONDS: Final = 2 * 60 * 60
_DERIVATIVE_STAGE_PREFIX: Final = ".pws-0114-m1-derivative-"
_DERIVATIVE_STAGE_SUFFIX: Final = ".tmp"
_WINDOWS_ALREADY_EXISTS_ERRORS: Final = frozenset({80, 183})
_WINDOWS_NO_MORE_FILES: Final = 18
_PROCESS_TREE_SAMPLE_ATTEMPTS: Final = 3
_SHA256_LENGTH: Final = 64
_VERSION_IDENTITY: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}\Z")
_GIT_EXECUTABLE: Final = Path(r"C:\Program Files\Git\cmd\git.exe")
_TRUSTED_PROGRAM_FILES_ROOT: Final = Path(r"C:\Program Files")
_QPDF_BIN_FILE_SHA256: Final = (
    ("concrt140.dll", "2405355f0a58067b258f8df33c327e3a3d716eaac5a3a5aebb757842d85bd376"),
    ("fix-qdf.exe", "f322a47cf83869bcfd3c7a1a60b173b89277d56f94ed22997c3eeb519be156b8"),
    ("msvcp140_1.dll", "bfad5aef4c63a669e3c140655cdfdf395b6c979b400a447bd5dcb65ed8826c3d"),
    ("msvcp140_2.dll", "3ea06f0ee098b4823cb79599df3780e7f23cce52c19aac31d2a0d47efe33a5e9"),
    (
        "msvcp140_atomic_wait.dll",
        "640b2aefced484d0368eea5bdd06addd0658a3a70a49256e560d6923b404a479",
    ),
    (
        "msvcp140_codecvt_ids.dll",
        "f2069a52880ec885ee7f0511186100eb7fada0411a2b4948fafea7735b878a18",
    ),
    ("msvcp140.dll", "0f885b509a685d2bbfa652fed26b5fb31d88fbdab0a978c641d1c7b8aa460aa9"),
    ("qpdf.exe", "43f79db620ce09529a67572a5de87aec4065b95f11ba6e5918db557f943a7eac"),
    ("qpdf30.dll", "623338ff5a9caab476f9e80ccc40c28c194208f4bc5d8e51eac7fca792e2e969"),
    (
        "vcruntime140_1.dll",
        "1f2d41c4aa5db0bc33ebf7b66d72943a817d7ce6cbe880502a9403823633093f",
    ),
    (
        "vcruntime140.dll",
        "d5e4d9a3e835fa679450145d6a7d94e36573a509317111904d9b3712c30d9066",
    ),
    (
        "zlib-flate.exe",
        "9573d60241869907fb1fd6df682b0327f2403e7e79f9f563c96f10fdeac6b3f5",
    ),
)
_GIT_FIXED_OPTIONS: Final = (
    "--no-replace-objects",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.untrackedCache=false",
)
_CHILD_ENVIRONMENT_NAMES: Final = frozenset(
    {
        "ALLUSERSPROFILE",
        "APPDATA",
        "COMMONPROGRAMFILES",
        "COMMONPROGRAMFILES(X86)",
        "COMMONPROGRAMW6432",
        "COMSPEC",
        "HOMEDRIVE",
        "HOMEPATH",
        "LOCALAPPDATA",
        "OS",
        "PATH",
        "PATHEXT",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "PWS_QPDF_EXECUTABLE",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERDOMAIN",
        "USERNAME",
        "USERPROFILE",
        "WINDIR",
    }
)
_CHILD_EVIDENCE_FIELDS: Final = frozenset(
    {
        "fixture_set_sha256",
        "source_set_sha256",
        "python_version",
        "pikepdf_version",
        "qpdf_version",
        "pdfium_version",
        "qt_version",
        "worker_config_sha256",
        "renderer_profile_sha256",
        "manifest_schema_version",
        "worker_protocol_version",
        "export_profile_version",
        "workload_version",
        "total_elapsed_ns",
        "source_count",
        "source_page_count",
        "active_page_count",
        "preview_count",
        "output_count",
        "output_page_count",
        "output_bytes",
        "output_sha256",
        "semantic_fingerprint",
        "deterministic_semantic_equal",
        "deterministic_output_equal",
        "verifier_codes",
        "recovery_status",
        "case_reopened",
        "reopened_page_count",
        "reopened_sequence_equal",
        "privacy_scan_status",
        "privacy_canary_count",
        "background_export_completed",
        "heartbeat_count",
        "maximum_heartbeat_gap_ns",
        "input_bytes",
        "peak_cache_bytes",
        "peak_temp_bytes",
        "max_heavy_operation_concurrency",
        "cancellation_acknowledgement_ms",
        "workers_terminated",
        "worker_recycle_policy",
    }
)


@dataclass(frozen=True, slots=True)
class LiveChildResult:
    """Validated path-free result from the one canonical pytest child."""

    phases: tuple[PhaseMeasurement, ...]
    workers: tuple[WorkerResourceMeasurement, ...]
    workspaces: tuple[WorkspaceUsageMeasurement, ...]
    evidence: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class PublishedLiveArtifacts:
    """Fixed local artifact paths returned only by the developer runner."""

    derivative: Path
    report: Path


class _DuplicateKeyError(ValueError):
    pass


class _FailedReportPublished(M1BenchmarkError):
    """Internal marker preserving a failure already committed as evidence."""


def _fail(code: str, message: str) -> NoReturn:
    error = M1BenchmarkError(code, message)
    try:
        raise error from None
    finally:
        error.__cause__ = None
        error.__context__ = None


def _fail_after_report(code: str, message: str) -> NoReturn:
    error = _FailedReportPublished(code, message)
    try:
        raise error from None
    finally:
        error.__cause__ = None
        error.__context__ = None


def _canonical_json(value: object) -> bytes:
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
        ).encode("ascii")
    except TypeError, ValueError:
        _fail("M1_LIVE_RESULT_INVALID", "The live result could not be encoded safely.")


def _identity_digest(values: object) -> str:
    return hashlib.sha256(_canonical_json(values).rstrip(b"\n")).hexdigest()


def _file_set_digest(paths: tuple[Path, ...]) -> str:
    identities = tuple(_bounded_file_sha256(path) for path in paths)
    return _identity_digest(identities)


def _config_value(value: object) -> object:
    if isinstance(value, Path):
        normalized = os.path.normcase(os.path.abspath(value))
        payload = {"path_sha256": hashlib.sha256(os.fsencode(normalized)).hexdigest()}
        try:
            if _is_reparse(value):
                _fail("M1_LIVE_CONFIG_INVALID", "A fixed worker resource is invalid.")
            is_file = value.is_file()
            is_directory = value.is_dir()
        except OSError:
            _fail("M1_LIVE_CONFIG_INVALID", "A fixed worker resource is unavailable.")
        if is_file:
            payload["kind"] = "file"
            payload["file_sha256"] = _bounded_file_sha256(value)
        elif is_directory:
            payload["kind"] = "directory"
        else:
            _fail("M1_LIVE_CONFIG_INVALID", "A fixed worker resource is unavailable.")
        return payload
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _config_value(getattr(value, item.name))
            for item in fields(value)
            if not item.name.startswith("_")
        }
    if isinstance(value, tuple):
        return [_config_value(item) for item in value]
    if type(value) is float:
        if not math.isfinite(value):
            _fail("M1_LIVE_CONFIG_INVALID", "The worker configuration is not finite.")
        return {"float_hex": value.hex()}
    if type(value) in {str, int, bool, type(None)}:
        return value
    _fail("M1_LIVE_CONFIG_INVALID", "The worker configuration cannot be bound safely.")


def production_worker_config_digest() -> str:
    """Bind every public validated field without serializing fixed resource paths."""

    export_config = build_windows_pdf_export_worker_config(
        resource_policy=CONSTRAINED_RESOURCE_POLICY_V1
    )
    with _open_qpdf_distribution_authority(export_config.qpdf_executable) as identity:
        return _identity_digest(
            (
                _config_value(_desktop_worker_config(CONSTRAINED_RESOURCE_POLICY_V1)),
                _config_value(export_config),
                identity,
            )
        )


def _bounded_file_sha256(path: Path) -> str:
    payload = _read_authenticated_file(
        path,
        maximum_bytes=MAX_DERIVATIVE_BYTES,
        unavailable_code="M1_LIVE_CONFIG_INVALID",
        unavailable_message="A fixed worker resource is unavailable.",
    )
    return hashlib.sha256(payload).hexdigest()


def _base_child_environment() -> dict[str, str]:
    return {
        name: value
        for name, value in os.environ.items()
        if name.upper() in _CHILD_ENVIRONMENT_NAMES
    }


def production_qpdf_version_identity() -> str:
    """Return the pinned export-engine version after binding its exact binary."""

    executable = build_windows_pdf_export_worker_config(
        resource_policy=CONSTRAINED_RESOURCE_POLICY_V1
    ).qpdf_executable
    with _open_qpdf_distribution_authority(executable):
        if _VERSION_IDENTITY.fullmatch(QPDF_EXPORT_ENGINE_VERSION) is None:
            _fail("M1_LIVE_CONFIG_INVALID", "The configured qpdf version is invalid.")
    return QPDF_EXPORT_ENGINE_VERSION


def _qpdf_distribution_identity(executable: Path) -> dict[str, object]:
    root = executable.parent
    expected_names = tuple(
        sorted((name for name, _digest in _QPDF_BIN_FILE_SHA256), key=str.casefold)
    )
    expected_executable = root / "qpdf.exe"
    if (
        os.path.normcase(os.path.abspath(executable))
        != os.path.normcase(os.path.abspath(expected_executable))
        or _qpdf_bin_names(root, maximum_entries=len(expected_names)) != expected_names
    ):
        _fail(
            "M1_LIVE_CONFIG_INVALID",
            "The configured qpdf distribution is not the approved release.",
        )
    identities: dict[str, object] = {}
    for name, digest in _QPDF_BIN_FILE_SHA256:
        identity = _config_value(root / name)
        if (
            not isinstance(identity, Mapping)
            or identity.get("kind") != "file"
            or identity.get("file_sha256") != digest
        ):
            _fail(
                "M1_LIVE_CONFIG_INVALID",
                "The configured qpdf distribution is not the approved release.",
            )
        identities[name] = dict(identity)
    if _qpdf_bin_names(root, maximum_entries=len(expected_names)) != expected_names:
        _fail(
            "M1_LIVE_CONFIG_INVALID",
            "The configured qpdf distribution is not the approved release.",
        )
    return {
        "files": identities,
        "version": QPDF_EXPORT_ENGINE_VERSION,
    }


def _qpdf_bin_names(root: Path, *, maximum_entries: int) -> tuple[str, ...]:
    if type(maximum_entries) is not int or maximum_entries < 1:
        _fail(
            "M1_LIVE_CONFIG_INVALID",
            "The configured qpdf distribution cannot be bounded safely.",
        )
    try:
        if _is_reparse(root):
            _fail(
                "M1_LIVE_CONFIG_INVALID",
                "The configured qpdf distribution is not the approved release.",
            )
        names: list[str] = []
        folded: set[str] = set()
        with os.scandir(root) as entries:
            for entry in entries:
                if len(names) >= maximum_entries:
                    _fail(
                        "M1_LIVE_CONFIG_INVALID",
                        "The configured qpdf distribution exceeds its safe entry limit.",
                    )
                normalized = entry.name.casefold()
                if (
                    normalized in folded
                    or entry.is_symlink()
                    or not entry.is_file(follow_symlinks=False)
                ):
                    _fail(
                        "M1_LIVE_CONFIG_INVALID",
                        "The configured qpdf distribution is not the approved release.",
                    )
                folded.add(normalized)
                names.append(entry.name)
    except OSError:
        _fail(
            "M1_LIVE_CONFIG_INVALID",
            "The configured qpdf distribution is unavailable.",
        )
    return tuple(sorted(names, key=str.casefold))


@contextmanager
def _open_qpdf_distribution_authority(
    executable: Path,
) -> Iterator[dict[str, object]]:
    maximum_entries = len(_QPDF_BIN_FILE_SHA256)
    expected_names = tuple(
        sorted((name for name, _digest in _QPDF_BIN_FILE_SHA256), key=str.casefold)
    )
    if _qpdf_bin_names(executable.parent, maximum_entries=maximum_entries) != expected_names:
        _fail(
            "M1_LIVE_CONFIG_INVALID",
            "The configured qpdf distribution is not the approved release.",
        )
    inventory: _ApprovedResourceInventory
    try:
        inventory = _open_approved_resource_inventory(
            (executable.parent,),
            acl_authority=False,
        )
    except Exception:
        _fail(
            "M1_LIVE_CONFIG_INVALID",
            "The configured qpdf distribution could not be authenticated.",
        )
    active_error = False
    try:
        identity = _qpdf_distribution_identity(executable)
        _require_qpdf_inventory_unchanged(inventory)
        yield identity
        _require_qpdf_inventory_unchanged(inventory)
    except M1BenchmarkError:
        active_error = True
        raise
    except Exception:
        active_error = True
        _fail(
            "M1_LIVE_CONFIG_INVALID",
            "The configured qpdf distribution changed unexpectedly.",
        )
    except BaseException:
        active_error = True
        raise
    finally:
        if not inventory.close_no_raise() and not active_error:
            _fail(
                "M1_LIVE_CONFIG_INVALID",
                "The configured qpdf distribution authority was not released safely.",
            )


def _require_qpdf_inventory_unchanged(inventory: _ApprovedResourceInventory) -> None:
    for resource in inventory.resources:
        _require_retained_resource_unchanged(resource)


def _require_live_qpdf_trusted_root(executable: Path) -> None:
    try:
        Path(os.path.abspath(executable)).relative_to(_TRUSTED_PROGRAM_FILES_ROOT)
    except OSError, ValueError:
        _fail(
            "M1_LIVE_CONFIG_INVALID",
            "The live qpdf distribution is outside the trusted installation root.",
        )


def _validate_child_identity(evidence: Mapping[str, object]) -> None:
    try:
        expected = {
            "fixture_set_sha256": _file_set_digest(LIVE_FIXTURES),
            "renderer_profile_sha256": PDF_THUMBNAIL_RENDERER_PROFILE_SHA256,
            "worker_config_sha256": production_worker_config_digest(),
            "qpdf_version": production_qpdf_version_identity(),
            "manifest_schema_version": CURRENT_MANIFEST_SCHEMA_VERSION,
            "worker_protocol_version": PROTOCOL_VERSION,
            "export_profile_version": PDF_EXPORT_PROFILE_VERSION,
        }
    except Exception:
        _fail("M1_LIVE_IDENTITY_INVALID", "The checked workload identity is unavailable.")
    if any(evidence.get(name) != value for name, value in expected.items()):
        _fail("M1_LIVE_IDENTITY_INVALID", "The child result is not bound to this workload.")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError
        result[key] = value
    return result


def _parse_uuid4(value: object) -> UUID:
    if type(value) is not str:
        _fail("M1_LIVE_RESULT_INVALID", "The live run identity is invalid.")
    try:
        parsed = UUID(value)
    except ValueError, TypeError, AttributeError:
        _fail("M1_LIVE_RESULT_INVALID", "The live run identity is invalid.")
    if parsed.version != 4 or str(parsed) != value:
        _fail("M1_LIVE_RESULT_INVALID", "The live run identity is invalid.")
    return parsed


def _child_session_from_environment() -> tuple[UUID, Path, bytes] | None:
    """Return the private runner handshake, or None for an ordinary test run."""

    observed = tuple(os.environ.get(name) for name in LIVE_ENV_NAMES)
    if all(value is None for value in observed):
        return None
    if any(value is None for value in observed):
        _fail("M1_LIVE_HANDSHAKE_INVALID", "The live-run handshake is incomplete.")
    protocol, raw_run_id, raw_stage, raw_key = cast(tuple[str, str, str, str], observed)
    if protocol != str(LIVE_PROTOCOL_VERSION):
        _fail("M1_LIVE_HANDSHAKE_INVALID", "The live-run protocol is unsupported.")
    run_id = _parse_uuid4(raw_run_id)
    stage = Path(raw_stage)
    try:
        stage_is_valid = (
            stage.is_absolute()
            and stage.name == "artifacts"
            and stage.parent.name == str(run_id)
            and stage.is_dir()
            and not _is_reparse(stage)
            and not _is_reparse(stage.parent)
        )
    except OSError:
        stage_is_valid = False
    if not stage_is_valid:
        _fail("M1_LIVE_HANDSHAKE_INVALID", "The live-run stage is invalid.")
    try:
        key = bytes.fromhex(raw_key)
    except ValueError:
        _fail("M1_LIVE_HANDSHAKE_INVALID", "The live-run handshake key is invalid.")
    if len(key) != 32:
        _fail("M1_LIVE_HANDSHAKE_INVALID", "The live-run handshake key is invalid.")
    return run_id, stage, key


def emit_live_child_artifacts(
    *,
    phases: tuple[PhaseMeasurement, ...],
    workers: tuple[WorkerResourceMeasurement, ...],
    workspaces: tuple[WorkspaceUsageMeasurement, ...],
    evidence: Mapping[str, object],
    derivative_source: Path,
) -> None:
    """Emit the private child envelope only for a complete runner handshake."""

    session = _child_session_from_environment()
    if session is None:
        return
    run_id, stage, key = session
    if (
        tuple(item.phase for item in phases) != tuple(M1Phase)
        or any(item.outcome is not PhaseOutcome.COMPLETED for item in phases)
        or tuple(item.operation for item in workers) != tuple(WorkerOperation)
        or tuple(item.checkpoint for item in workspaces) != tuple(WorkspaceCheckpoint)
        or set(evidence) != _CHILD_EVIDENCE_FIELDS
        or not derivative_source.is_file()
        or derivative_source.is_symlink()
    ):
        _fail("M1_LIVE_RESULT_INVALID", "The completed live result is incomplete.")
    derivative = _read_authenticated_file(
        derivative_source,
        maximum_bytes=MAX_DERIVATIVE_BYTES,
        unavailable_code="M1_LIVE_RESULT_INVALID",
        unavailable_message="The verified derivative is unavailable.",
    )
    if not 1 <= len(derivative) <= MAX_DERIVATIVE_BYTES:
        _fail("M1_LIVE_RESULT_INVALID", "The verified derivative has an invalid size.")
    output_sha256 = evidence.get("output_sha256")
    output_bytes = evidence.get("output_bytes")
    if output_bytes != len(derivative) or output_sha256 != hashlib.sha256(derivative).hexdigest():
        _fail("M1_LIVE_RESULT_INVALID", "The verified derivative identity is inconsistent.")
    body = {
        "protocol_version": LIVE_PROTOCOL_VERSION,
        "run_id": str(run_id),
        "phase_measurements": [
            {
                "phase": item.phase.value,
                "outcome": item.outcome.value,
                "elapsed_ns": item.elapsed_ns,
            }
            for item in phases
        ],
        "worker_resource_measurements": [
            {
                "operation": item.operation.value,
                "process_memory_limit_bytes": item.process_memory_limit_bytes,
                "job_memory_limit_bytes": item.job_memory_limit_bytes,
                "peak_process_memory_used_bytes": item.peak_process_memory_used_bytes,
                "peak_job_memory_used_bytes": item.peak_job_memory_used_bytes,
            }
            for item in workers
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
            for item in workspaces
        ],
        "evidence": dict(evidence),
    }
    signature = hmac.new(key, _canonical_json(body), hashlib.sha256).hexdigest()
    envelope = {**body, "hmac_sha256": signature}
    result_bytes = _canonical_json(envelope)
    if len(result_bytes) > MAX_CHILD_RESULT_BYTES:
        _fail("M1_LIVE_RESULT_INVALID", "The live result exceeds its size bound.")
    derivative_destination = stage / LIVE_CHILD_DERIVATIVE_FILENAME
    result_destination = stage / LIVE_CHILD_RESULT_FILENAME
    stage_handle: WindowsHandle | None = None
    derivative_handle: WindowsHandle | None = None
    result_handle: WindowsHandle | None = None
    try:
        stage_handle = open_handle(stage, traverse=True)
        require_expected_handle(stage_handle, stage, directory=True)
        derivative_handle = write_new_file(derivative_destination, derivative)
        require_expected_handle(stage_handle, stage, directory=True)
        require_expected_handle(derivative_handle, derivative_destination, directory=False)
        if derivative_handle.info.link_count != 1:
            _fail("M1_LIVE_RESULT_WRITE_FAILED", "The live result stage is invalid.")
        result_handle = write_new_file(result_destination, result_bytes)
        require_expected_handle(stage_handle, stage, directory=True)
        require_expected_handle(result_handle, result_destination, directory=False)
        if result_handle.info.link_count != 1:
            _fail("M1_LIVE_RESULT_WRITE_FAILED", "The live result stage is invalid.")
    except M1BenchmarkError:
        raise
    except WorkspaceError, OSError:
        _fail("M1_LIVE_RESULT_WRITE_FAILED", "The live result could not be staged safely.")
    finally:
        for handle in (result_handle, derivative_handle, stage_handle):
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    pass


def _decode_child_result(payload: bytes, *, run_id: UUID, key: bytes) -> LiveChildResult:
    if not 1 <= len(payload) <= MAX_CHILD_RESULT_BYTES:
        _fail("M1_LIVE_RESULT_INVALID", "The live result has an invalid size.")
    try:
        loaded = json.loads(payload.decode("ascii"), object_pairs_hook=_unique_object)
    except UnicodeDecodeError, json.JSONDecodeError, _DuplicateKeyError, ValueError:
        _fail("M1_LIVE_RESULT_INVALID", "The live result is not strict canonical JSON.")
    if type(loaded) is not dict or set(loaded) != {
        "protocol_version",
        "run_id",
        "phase_measurements",
        "worker_resource_measurements",
        "workspace_usage_measurements",
        "evidence",
        "hmac_sha256",
    }:
        _fail("M1_LIVE_RESULT_INVALID", "The live result uses an unexpected field set.")
    if payload != _canonical_json(loaded):
        _fail("M1_LIVE_RESULT_INVALID", "The live result is not canonical.")
    if (
        loaded["protocol_version"] != LIVE_PROTOCOL_VERSION
        or _parse_uuid4(loaded["run_id"]) != run_id
    ):
        _fail("M1_LIVE_RESULT_INVALID", "The live result does not match this invocation.")
    signature = loaded["hmac_sha256"]
    if type(signature) is not str or len(signature) != _SHA256_LENGTH:
        _fail("M1_LIVE_RESULT_INVALID", "The live result signature is invalid.")
    body = dict(loaded)
    del body["hmac_sha256"]
    expected_signature = hmac.new(key, _canonical_json(body), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        _fail("M1_LIVE_RESULT_INVALID", "The live result signature is invalid.")
    try:
        phases = tuple(
            PhaseMeasurement(
                M1Phase(item["phase"]),
                PhaseOutcome(item["outcome"]),
                item["elapsed_ns"],
            )
            for item in loaded["phase_measurements"]
        )
        workers = tuple(
            WorkerResourceMeasurement(
                WorkerOperation(item["operation"]),
                item["process_memory_limit_bytes"],
                item["job_memory_limit_bytes"],
                item["peak_process_memory_used_bytes"],
                item["peak_job_memory_used_bytes"],
            )
            for item in loaded["worker_resource_measurements"]
        )
        workspaces = tuple(
            WorkspaceUsageMeasurement(
                WorkspaceCheckpoint(item["checkpoint"]),
                item["case_bytes"],
                item["cache_bytes"],
                item["temp_bytes"],
                item["entry_count"],
                item["maximum_depth_seen"],
            )
            for item in loaded["workspace_usage_measurements"]
        )
    except KeyError, TypeError, ValueError, M1BenchmarkError:
        _fail("M1_LIVE_RESULT_INVALID", "The live result measurements are invalid.")
    evidence = loaded["evidence"]
    if (
        type(evidence) is not dict
        or set(evidence) != _CHILD_EVIDENCE_FIELDS
        or tuple(item.phase for item in phases) != tuple(M1Phase)
        or any(item.outcome is not PhaseOutcome.COMPLETED for item in phases)
        or tuple(item.operation for item in workers) != tuple(WorkerOperation)
        or tuple(item.checkpoint for item in workspaces) != tuple(WorkspaceCheckpoint)
    ):
        _fail("M1_LIVE_RESULT_INVALID", "The live result measurements are incomplete.")
    return LiveChildResult(phases, workers, workspaces, cast(Mapping[str, object], evidence))


def _git_text(*arguments: str) -> str:
    environment = {
        name: value for name, value in os.environ.items() if not name.upper().startswith("GIT_")
    }
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_OPTIONAL_LOCKS": "0",
        }
    )
    try:
        if not _GIT_EXECUTABLE.is_file() or _is_reparse(_GIT_EXECUTABLE):
            _fail("M1_LIVE_GIT_INVALID", "The repository identity could not be verified.")
    except OSError:
        _fail("M1_LIVE_GIT_INVALID", "The repository identity could not be verified.")
    try:
        completed = subprocess.run(
            (str(_GIT_EXECUTABLE), *_GIT_FIXED_OPTIONS, *arguments),
            cwd=PROJECT_ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            env=environment,
            timeout=30,
            shell=False,
        )
    except OSError, subprocess.SubprocessError:
        _fail("M1_LIVE_GIT_INVALID", "The repository identity could not be verified.")
    if completed.returncode != 0:
        _fail("M1_LIVE_GIT_INVALID", "The repository identity could not be verified.")
    return completed.stdout.strip()


def _git_identity() -> tuple[str, str]:
    top_level = _git_text("rev-parse", "--show-toplevel")
    try:
        authenticated_top_level = Path(top_level).resolve(strict=True)
        expected_top_level = PROJECT_ROOT.resolve(strict=True)
    except OSError:
        _fail("M1_LIVE_GIT_INVALID", "The repository identity could not be verified.")
    if authenticated_top_level != expected_top_level:
        _fail("M1_LIVE_GIT_INVALID", "The repository identity could not be verified.")
    index_flags = _git_text("ls-files", "-v")
    if any(
        line and (line[0].islower() or line.startswith("S ")) for line in index_flags.splitlines()
    ):
        _fail(
            "M1_LIVE_GIT_INVALID",
            "The repository index contains hidden tracked-file state.",
        )
    status = _git_text("status", "--porcelain=v1", "--untracked-files=all")
    if status:
        _fail("M1_LIVE_GIT_DIRTY", "The live runner requires a clean repository worktree.")
    revision = _git_text("rev-parse", "HEAD")
    tree = _git_text("rev-parse", "HEAD^{tree}")
    if len(revision) not in {40, 64} or len(tree) not in {40, 64}:
        _fail("M1_LIVE_GIT_INVALID", "The repository identity could not be verified.")
    return revision, tree


def _child_environment(*, run_id: UUID, stage: Path, key: bytes) -> dict[str, str]:
    environment = _base_child_environment()
    environment.update(
        {
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            LIVE_ENV_PROTOCOL: str(LIVE_PROTOCOL_VERSION),
            LIVE_ENV_RUN_ID: str(run_id),
            LIVE_ENV_STAGE: str(stage),
            LIVE_ENV_HMAC_KEY: key.hex(),
            RESOURCE_PROFILE_ENVIRONMENT_VARIABLE: CONSTRAINED_RESOURCE_POLICY_V1.profile.value,
        }
    )
    return environment


def _is_reparse(path: Path) -> bool:
    information = path.lstat()
    attributes = getattr(information, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & reparse_flag)


def _prepare_work_directory(path: Path) -> Path:
    if not path.is_absolute():
        _fail("M1_LIVE_WORK_INVALID", "The live work directory must be absolute.")
    try:
        path.mkdir(parents=True, exist_ok=True)
        resolved = path.resolve(strict=True)
        if resolved != path.resolve() or not resolved.is_dir() or _is_reparse(resolved):
            _fail("M1_LIVE_WORK_INVALID", "The live work directory is invalid.")
    except M1BenchmarkError:
        raise
    except OSError:
        _fail("M1_LIVE_WORK_INVALID", "The live work directory is unavailable.")
    return resolved


def _read_authenticated_file(
    path: Path,
    *,
    maximum_bytes: int,
    unavailable_code: str,
    unavailable_message: str,
) -> bytes:
    reader: WindowsHandle | None = None
    try:
        reader = open_handle(path, read=True)
        require_expected_handle(reader, path, directory=False)
        opened = reader.info
        current = reader.refresh_info()
        if (
            current.identity != opened.identity
            or current.is_directory
            or current.is_reparse_point
            or current.is_sparse
            or current.link_count != 1
            or not 1 <= current.logical_size <= maximum_bytes
        ):
            _fail(unavailable_code, unavailable_message)
        return reader.read_bytes(maximum_bytes=maximum_bytes)
    except M1BenchmarkError:
        raise
    except WorkspaceError, OSError:
        _fail(unavailable_code, unavailable_message)
    finally:
        if reader is not None:
            try:
                reader.close()
            except Exception:
                pass


def _read_child_derivative(path: Path, *, expected_sha256: object, expected_bytes: object) -> bytes:
    payload = _read_authenticated_file(
        path,
        maximum_bytes=MAX_DERIVATIVE_BYTES,
        unavailable_code="M1_LIVE_RESULT_INVALID",
        unavailable_message="The staged derivative is unavailable.",
    )
    if (
        not 1 <= len(payload) <= MAX_DERIVATIVE_BYTES
        or expected_bytes != len(payload)
        or expected_sha256 != hashlib.sha256(payload).hexdigest()
        or not payload.startswith(b"%PDF-")
    ):
        _fail("M1_LIVE_RESULT_INVALID", "The staged derivative identity is invalid.")
    return payload


def _discard_handle(handle: WindowsHandle | None) -> None:
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


def _publish_derivative(
    output_directory: Path,
    derivative: bytes,
    *,
    directory_handle: WindowsHandle,
) -> tuple[Path, WindowsHandle]:
    derivative_handle: WindowsHandle | None = None
    try:
        require_expected_handle(directory_handle, output_directory, directory=True)
        destination = output_directory / LIVE_DERIVATIVE_FILENAME
        stage = output_directory / (
            f"{_DERIVATIVE_STAGE_PREFIX}{uuid4().hex}{_DERIVATIVE_STAGE_SUFFIX}"
        )
        derivative_handle = write_new_file(stage, derivative)
        identity = derivative_handle.info.identity
        _validate_derivative(
            stage,
            expected_identity=identity,
            expected_payload=derivative,
        )
        published, error_code = rename_open_file_no_replace_at(
            derivative_handle,
            directory_handle,
            destination_name=LIVE_DERIVATIVE_FILENAME,
        )
        if not published:
            code = (
                "M1_LIVE_OUTPUT_EXISTS"
                if error_code in _WINDOWS_ALREADY_EXISTS_ERRORS
                else "M1_LIVE_OUTPUT_FAILED"
            )
            _fail(code, "The fixed live derivative destination could not be published.")
        current = derivative_handle.refresh_info()
        require_expected_handle(
            derivative_handle,
            destination,
            directory=False,
            expected_identity=identity,
        )
        if current.identity != identity or current.link_count != 1:
            _fail("M1_LIVE_OUTPUT_FAILED", "The live derivative changed during publication.")
        _validate_derivative(
            destination,
            expected_identity=identity,
            expected_payload=derivative,
        )
        return destination, derivative_handle
    except M1BenchmarkError:
        _discard_handle(derivative_handle)
        raise
    except WorkspaceError, OSError:
        _discard_handle(derivative_handle)
        _fail("M1_LIVE_OUTPUT_FAILED", "The live derivative could not be published safely.")
    except BaseException:
        _discard_handle(derivative_handle)
        raise


def _validate_derivative(
    path: Path,
    *,
    expected_identity: FileIdentity,
    expected_payload: bytes,
) -> None:
    reader: WindowsHandle | None = None
    try:
        reader = open_handle(path, read=True, share_delete=True, share_write=True)
        require_expected_handle(
            reader,
            path,
            directory=False,
            expected_identity=expected_identity,
        )
        current = reader.refresh_info()
        if current.is_reparse_point or current.is_sparse or current.link_count != 1:
            _fail("M1_LIVE_OUTPUT_FAILED", "The live derivative changed during validation.")
        reopened = reader.read_bytes(maximum_bytes=MAX_DERIVATIVE_BYTES)
        if reopened != expected_payload:
            _fail("M1_LIVE_OUTPUT_FAILED", "The live derivative failed reopen validation.")
    finally:
        if reader is not None:
            reader.close()


def _require_fixed_outputs_absent(output_directory: Path) -> None:
    try:
        if (
            not output_directory.is_absolute()
            or not output_directory.is_dir()
            or _is_reparse(output_directory)
        ):
            _fail("M1_LIVE_OUTPUT_FAILED", "The fixed live output directory is invalid.")
    except M1BenchmarkError:
        raise
    except OSError:
        _fail("M1_LIVE_OUTPUT_FAILED", "The fixed live output directory is unavailable.")
    for name in (LIVE_DERIVATIVE_FILENAME, LIVE_REPORT_FILENAME):
        try:
            (output_directory / name).lstat()
        except FileNotFoundError:
            continue
        except OSError:
            _fail("M1_LIVE_OUTPUT_FAILED", "The fixed live output state is unavailable.")
        _fail("M1_LIVE_OUTPUT_EXISTS", "The fixed live output already exists and was preserved.")


def _cleanup_run_directory(
    run_directory: Path,
    work_directory: Path,
    authority: WindowsHandle,
) -> None:
    try:
        require_expected_handle(authority, run_directory, directory=True)
        original = authority.info
        current = authority.refresh_info()
        if current.identity != original.identity or current.is_reparse_point:
            _fail("M1_LIVE_CLEANUP_FAILED", "The private live-run stage changed unexpectedly.")
        resolved_run = run_directory.resolve(strict=True)
        resolved_work = work_directory.resolve(strict=True)
        if resolved_run.parent != resolved_work or not run_directory.name:
            _fail("M1_LIVE_CLEANUP_FAILED", "The private live-run stage changed unexpectedly.")
        for root, directories, filenames in os.walk(
            run_directory,
            topdown=True,
            followlinks=False,
        ):
            for name in (*directories, *filenames):
                candidate = Path(root) / name
                if _is_reparse(candidate):
                    _fail(
                        "M1_LIVE_CLEANUP_FAILED",
                        "The private live-run stage contains an unsafe object.",
                    )
        for child in tuple(run_directory.iterdir()):
            if _is_reparse(child):
                _fail(
                    "M1_LIVE_CLEANUP_FAILED",
                    "The private live-run stage contains an unsafe object.",
                )
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        require_expected_handle(
            authority,
            run_directory,
            directory=True,
            expected_identity=original.identity,
        )
        if tuple(run_directory.iterdir()):
            _fail("M1_LIVE_CLEANUP_FAILED", "The private live-run stage was not emptied.")
        authority.mark_for_deletion()
        authority.close()
        try:
            run_directory.lstat()
        except FileNotFoundError:
            return
        _fail("M1_LIVE_CLEANUP_FAILED", "The private live-run stage was not removed.")
    except M1BenchmarkError:
        try:
            authority.close()
        except BaseException:
            pass
        raise
    except WorkspaceError, OSError:
        try:
            authority.close()
        except BaseException:
            pass
        _fail("M1_LIVE_CLEANUP_FAILED", "The private live-run stage could not be removed safely.")
    except BaseException:
        try:
            authority.close()
        except BaseException:
            pass
        raise


def _run_canonical_child(
    *,
    run_id: UUID,
    run_directory: Path,
    work_directory: Path,
    artifacts: Path,
    basetemp: Path,
    key: bytes,
    run_authority: WindowsHandle,
) -> tuple[LiveChildResult, bytes, int]:
    try:
        try:
            qpdf_executable = build_windows_pdf_export_worker_config(
                resource_policy=CONSTRAINED_RESOURCE_POLICY_V1
            ).qpdf_executable
        except Exception:
            _fail(
                "M1_LIVE_CONFIG_INVALID",
                "The live qpdf distribution is unavailable.",
            )
        _require_live_qpdf_trusted_root(qpdf_executable)
        with _open_qpdf_distribution_authority(qpdf_executable):
            return _execute_canonical_child(
                run_id=run_id,
                artifacts=artifacts,
                basetemp=basetemp,
                key=key,
            )
    finally:
        _cleanup_run_directory(run_directory, work_directory, run_authority)


def _execute_canonical_child(
    *,
    run_id: UUID,
    artifacts: Path,
    basetemp: Path,
    key: bytes,
) -> tuple[LiveChildResult, bytes, int]:
    environment = _child_environment(run_id=run_id, stage=artifacts, key=key)
    peak_process_tree_rss_bytes = 0
    try:
        process = subprocess.Popen(
            (
                sys.executable,
                "-I",
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "--basetemp",
                str(basetemp),
                *LIVE_REQUIRED_TEST_NODES,
            ),
            cwd=PROJECT_ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
        deadline = time.monotonic() + LIVE_RUN_TIMEOUT_SECONDS
        while process.poll() is None:
            peak_process_tree_rss_bytes = max(
                peak_process_tree_rss_bytes,
                _windows_process_tree_rss_bytes(process),
            )
            if time.monotonic() >= deadline:
                process.kill()
                process.wait(timeout=30)
                _fail(
                    "M1_LIVE_CHILD_FAILED",
                    "The canonical vertical-slice child did not finish safely.",
                )
            time.sleep(CONSTRAINED_RESOURCE_POLICY_V1.resource_sampling_interval_ms / 1_000)
    except OSError, subprocess.SubprocessError:
        _fail(
            "M1_LIVE_CHILD_FAILED",
            "The canonical vertical-slice child did not finish safely.",
        )
    if process.returncode != 0:
        _fail("M1_LIVE_CHILD_FAILED", "The canonical vertical-slice child did not pass.")
    result_payload = _read_authenticated_file(
        artifacts / LIVE_CHILD_RESULT_FILENAME,
        maximum_bytes=MAX_CHILD_RESULT_BYTES,
        unavailable_code="M1_LIVE_RESULT_INVALID",
        unavailable_message="The canonical live result is unavailable.",
    )
    child = _decode_child_result(result_payload, run_id=run_id, key=key)
    _validate_child_identity(child.evidence)
    derivative = _read_child_derivative(
        artifacts / LIVE_CHILD_DERIVATIVE_FILENAME,
        expected_sha256=child.evidence.get("output_sha256"),
        expected_bytes=child.evidence.get("output_bytes"),
    )
    if peak_process_tree_rss_bytes <= 0:
        _fail("M1_RESOURCE_MEASUREMENT_FAILED", "Process-tree memory could not be measured.")
    return child, derivative, peak_process_tree_rss_bytes


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = (
        ("cb", ctypes.c_ulong),
        ("page_fault_count", ctypes.c_ulong),
        ("peak_working_set_size", ctypes.c_size_t),
        ("working_set_size", ctypes.c_size_t),
        ("quota_peak_paged_pool_usage", ctypes.c_size_t),
        ("quota_paged_pool_usage", ctypes.c_size_t),
        ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
        ("quota_non_paged_pool_usage", ctypes.c_size_t),
        ("pagefile_usage", ctypes.c_size_t),
        ("peak_pagefile_usage", ctypes.c_size_t),
    )


class _ProcessEntry32W(ctypes.Structure):
    _fields_ = (
        ("dwSize", ctypes.c_ulong),
        ("cntUsage", ctypes.c_ulong),
        ("th32ProcessID", ctypes.c_ulong),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", ctypes.c_ulong),
        ("cntThreads", ctypes.c_ulong),
        ("th32ParentProcessID", ctypes.c_ulong),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.c_ulong),
        ("szExeFile", ctypes.c_wchar * 260),
    )


def _descendant_process_ids(parent_by_pid: Mapping[int, int], root_pid: int) -> frozenset[int]:
    """Resolve one whole process-snapshot ancestry closure."""

    descendants = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, parent_pid in parent_by_pid.items():
            if parent_pid in descendants and pid not in descendants:
                descendants.add(pid)
                changed = True
    return frozenset(descendants)


def _complete_process_tree_rss(
    expected_pids: frozenset[int],
    rss_by_pid: Mapping[int, int],
) -> int | None:
    """Reject a sample unless every snapshotted process has one valid RSS reading."""

    if (
        not expected_pids
        or set(rss_by_pid) != set(expected_pids)
        or any(type(value) is not int or value < 0 for value in rss_by_pid.values())
    ):
        return None
    return sum(rss_by_pid.values())


def _process_snapshot_enumeration_completed(last_error: object) -> bool:
    """Accept only the documented Process32Next end-of-enumeration status."""

    return type(last_error) is int and last_error == _WINDOWS_NO_MORE_FILES


def _windows_process_tree_rss_bytes(process: subprocess.Popen[bytes]) -> int:
    """Return one complete tree sample, retrying snapshots invalidated by process exit."""

    if os.name != "nt":
        _fail("M1_LIVE_PLATFORM_UNSUPPORTED", "Process-tree measurement requires Windows.")
    for _attempt in range(_PROCESS_TREE_SAMPLE_ATTEMPTS):
        measured = _windows_process_tree_rss_sample(process)
        if measured is not None:
            return measured
    _fail("M1_RESOURCE_MEASUREMENT_FAILED", "Process-tree memory could not be measured.")


def _windows_process_tree_rss_sample(process: subprocess.Popen[bytes]) -> int | None:
    """Sum a whole Toolhelp snapshot, rejecting any incomplete enumeration or PID read."""

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_snapshot = kernel32.CreateToolhelp32Snapshot
        create_snapshot.argtypes = (ctypes.c_ulong, ctypes.c_ulong)
        create_snapshot.restype = ctypes.c_void_p
        process_first = kernel32.Process32FirstW
        process_first.argtypes = (ctypes.c_void_p, ctypes.POINTER(_ProcessEntry32W))
        process_first.restype = ctypes.c_int
        process_next = kernel32.Process32NextW
        process_next.argtypes = (ctypes.c_void_p, ctypes.POINTER(_ProcessEntry32W))
        process_next.restype = ctypes.c_int
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (ctypes.c_void_p,)
        close_handle.restype = ctypes.c_int
        open_process = kernel32.OpenProcess
        open_process.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
        open_process.restype = ctypes.c_void_p
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        query = psapi.GetProcessMemoryInfo
        query.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)
        query.restype = ctypes.c_int
    except AttributeError, OSError:
        _fail("M1_RESOURCE_MEASUREMENT_FAILED", "Process-tree memory could not be measured.")
    snapshot = create_snapshot(0x00000002, 0)
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        _fail("M1_RESOURCE_MEASUREMENT_FAILED", "Process-tree memory could not be measured.")
    parents: dict[int, int] = {}
    entry = _ProcessEntry32W()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        if not process_first(snapshot, ctypes.byref(entry)):
            return None
        while True:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            entry.dwSize = ctypes.sizeof(entry)
            ctypes.set_last_error(0)
            if not process_next(snapshot, ctypes.byref(entry)):
                break
        if not _process_snapshot_enumeration_completed(ctypes.get_last_error()):
            return None
    finally:
        close_handle(snapshot)
    pids = _descendant_process_ids(parents, process.pid)
    rss_by_pid: dict[int, int] = {}
    for pid in pids:
        owned = pid != process.pid
        handle = open_process(0x0410, 0, pid) if owned else getattr(process, "_handle", 0)
        if not handle:
            return None
        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        try:
            if not query(handle, ctypes.byref(counters), counters.cb):
                return None
            rss_by_pid[pid] = int(counters.working_set_size)
        finally:
            if owned:
                close_handle(handle)
    return _complete_process_tree_rss(pids, rss_by_pid)


def _publish_fixed_bundle(
    *,
    output_directory: Path,
    derivative: bytes,
    writer: _LiveM1ReportWriter,
    report: M1BenchmarkReport,
) -> PublishedLiveArtifacts:
    if report.vertical_slice.run_status is not VerticalSliceRunStatus.COMPLETED:
        _fail("M1_LIVE_RESULT_INVALID", "The completed report was not constructed.")
    derivative_handle: WindowsHandle | None = None
    derivative_destination: Path | None = None
    try:
        derivative_destination, derivative_handle = _publish_derivative(
            output_directory,
            derivative,
            directory_handle=writer._directory_handle,
        )
        report_destination = writer._publish_completed_report(report)
    except BaseException:
        if derivative_handle is not None:
            if writer._published:
                try:
                    derivative_handle.close()
                except BaseException:
                    pass
            else:
                _discard_handle(derivative_handle)
        raise
    try:
        derivative_handle.close()
    except BaseException:
        # The report is the commit marker and has already been reopened.  A
        # later handle-release fault cannot invalidate or delete its derivative.
        pass
    assert derivative_destination is not None
    return PublishedLiveArtifacts(derivative_destination, report_destination)


def _failed_evidence(
    *,
    run_id: UUID,
    failure_stage: FailureStage,
    started_ns: int,
    revision: str,
    tree: str,
    budget_results: tuple[BudgetResult, ...] = (),
) -> FailedRunEvidence:
    """Build path-free identity for one runner failure at an exact stage."""

    return FailedRunEvidence(
        run_id=run_id,
        failure_stage=failure_stage,
        total_elapsed_ns=max(1, time.perf_counter_ns() - started_ns),
        repository_revision=revision,
        repository_tree=tree,
        repository_worktree_clean=True,
        report_schema_sha256=_bounded_file_sha256(
            PROJECT_ROOT / "schemas/m1-vertical-slice-benchmark-report-v2.schema.json"
        ),
        budget_results=budget_results,
    )


def _publish_failed_live_report(
    *,
    output_directory: Path,
    policy_report: M1BenchmarkReport,
    result_code: str,
    failed_evidence: FailedRunEvidence,
    phases: tuple[PhaseMeasurement, ...] = (),
    workers: tuple[WorkerResourceMeasurement, ...] = (),
    workspaces: tuple[WorkspaceUsageMeasurement, ...] = (),
) -> Path:
    """Publish one sanitized failed report and no derivative."""

    with _open_live_m1_report_writer(output_directory) as writer:
        report = writer._build_failed_report(
            policy_report,
            result_code=result_code,
            phase_measurements=phases,
            worker_resource_measurements=workers,
            workspace_usage_measurements=workspaces,
            failed_evidence=failed_evidence,
        )
        return writer._publish_failed_report(report)


def _failure_stage_for_code(code: str) -> FailureStage:
    """Classify only pre-workload configuration failures as setup failures."""

    if code in {
        "M1_LIVE_CONFIG_INVALID",
        "M1_LIVE_WORK_INVALID",
        "M1_RESOURCE_POLICY_INVALID",
    }:
        return FailureStage.SETUP
    return FailureStage.WORKLOAD


def run_live_m1_vertical_slice(
    *,
    work_directory: Path,
    resource_policy: ResourcePolicy = CONSTRAINED_RESOURCE_POLICY_V1,
) -> PublishedLiveArtifacts:
    """Run the fixed workload under enforced constrained resource budgets."""

    if os.name != "nt":
        _fail("M1_LIVE_PLATFORM_UNSUPPORTED", "The live M1 runner requires Windows.")
    if resource_policy is not CONSTRAINED_RESOURCE_POLICY_V1:
        _fail(
            "M1_RESOURCE_POLICY_INVALID",
            "The canonical M1 run requires the checked-in constrained policy.",
        )
    revision_before, tree_before = _git_identity()
    run_started_ns = time.perf_counter_ns()
    output_directory = LIVE_OUTPUT_DIRECTORY
    try:
        output_directory.mkdir(exist_ok=True)
    except OSError:
        _fail("M1_LIVE_OUTPUT_FAILED", "The fixed live output directory is unavailable.")
    _require_fixed_outputs_absent(output_directory)
    run_id = uuid4()
    policy_report = build_m1_benchmark_report(resource_policy)
    try:
        return _run_live_m1_vertical_slice(
            work_directory=work_directory,
            resource_policy=resource_policy,
            output_directory=output_directory,
            policy_report=policy_report,
            run_id=run_id,
            run_started_ns=run_started_ns,
            revision_before=revision_before,
            tree_before=tree_before,
        )
    except M1BenchmarkError as error:
        if not isinstance(error, _FailedReportPublished):
            try:
                _publish_failed_live_report(
                    output_directory=output_directory,
                    policy_report=policy_report,
                    result_code=error.code,
                    failed_evidence=_failed_evidence(
                        run_id=run_id,
                        failure_stage=_failure_stage_for_code(error.code),
                        started_ns=run_started_ns,
                        revision=revision_before,
                        tree=tree_before,
                    ),
                )
            except M1BenchmarkError:
                pass
        raise


def _run_live_m1_vertical_slice(
    *,
    work_directory: Path,
    resource_policy: ResourcePolicy,
    output_directory: Path,
    policy_report: M1BenchmarkReport,
    run_id: UUID,
    run_started_ns: int,
    revision_before: str,
    tree_before: str,
) -> PublishedLiveArtifacts:
    """Execute one authenticated live run after the publication boundary is ready."""

    selected_work = _prepare_work_directory(work_directory)
    run_directory = selected_work / str(run_id)
    artifacts = run_directory / "artifacts"
    basetemp = run_directory / "pytest"
    run_authority: WindowsHandle | None = None
    try:
        run_directory.mkdir()
        run_authority = open_handle(run_directory, delete=True, traverse=True)
        require_expected_handle(run_authority, run_directory, directory=True)
        artifacts.mkdir()
    except WorkspaceError, OSError:
        if run_authority is not None:
            try:
                _cleanup_run_directory(run_directory, selected_work, run_authority)
            except M1BenchmarkError:
                pass
        else:
            try:
                run_directory.rmdir()
            except OSError:
                pass
        _fail("M1_LIVE_WORK_INVALID", "The private live-run stage could not be created.")
    except BaseException:
        if run_authority is not None:
            try:
                _cleanup_run_directory(run_directory, selected_work, run_authority)
            except BaseException:
                pass
        else:
            try:
                run_directory.rmdir()
            except OSError:
                pass
        raise
    assert run_authority is not None
    key = os.urandom(32)
    child, derivative, peak_process_tree_rss_bytes = _run_canonical_child(
        run_id=run_id,
        run_directory=run_directory,
        work_directory=selected_work,
        artifacts=artifacts,
        basetemp=basetemp,
        key=key,
        run_authority=run_authority,
    )
    revision_after, tree_after = _git_identity()
    if revision_after != revision_before or tree_after != tree_before:
        _fail("M1_LIVE_GIT_CHANGED", "The repository changed during the live run.")
    evidence_payload = dict(child.evidence)
    peak_cache_bytes = cast(int, evidence_payload["peak_cache_bytes"])
    peak_temp_bytes = cast(int, evidence_payload["peak_temp_bytes"])
    maximum_heartbeat_gap_ns = cast(int, evidence_payload["maximum_heartbeat_gap_ns"])
    total_elapsed_ns = cast(int, evidence_payload["total_elapsed_ns"])
    max_heavy_operation_concurrency = cast(int, evidence_payload["max_heavy_operation_concurrency"])
    cancellation_acknowledgement_ms = cast(int, evidence_payload["cancellation_acknowledgement_ms"])
    combined_cache_budget = (
        resource_policy.maximum_cache_bytes + resource_policy.thumbnail_preview_cache_bytes
    )
    budget_results = (
        BudgetResult(
            BudgetId.PROCESS_TREE_RSS,
            peak_process_tree_rss_bytes,
            resource_policy.canonical_process_tree_rss_bytes,
            peak_process_tree_rss_bytes <= resource_policy.canonical_process_tree_rss_bytes,
        ),
        BudgetResult(
            BudgetId.CACHE_BYTES,
            peak_cache_bytes,
            combined_cache_budget,
            peak_cache_bytes <= combined_cache_budget,
        ),
        BudgetResult(
            BudgetId.TEMP_BYTES,
            peak_temp_bytes,
            resource_policy.maximum_temporary_storage_bytes,
            peak_temp_bytes <= resource_policy.maximum_temporary_storage_bytes,
        ),
        BudgetResult(
            BudgetId.HEAVY_CONCURRENCY,
            max_heavy_operation_concurrency,
            resource_policy.maximum_heavy_operation_concurrency,
            max_heavy_operation_concurrency <= resource_policy.maximum_heavy_operation_concurrency,
        ),
        BudgetResult(
            BudgetId.UI_HEARTBEAT,
            _ceil_milliseconds(maximum_heartbeat_gap_ns),
            resource_policy.maximum_ui_heartbeat_gap_ms,
            maximum_heartbeat_gap_ns <= resource_policy.maximum_ui_heartbeat_gap_ms * 1_000_000,
        ),
        BudgetResult(
            BudgetId.CANCELLATION_ACKNOWLEDGEMENT,
            cancellation_acknowledgement_ms,
            resource_policy.cancellation_acknowledgement_ms,
            cancellation_acknowledgement_ms <= resource_policy.cancellation_acknowledgement_ms,
        ),
    )
    if any(not result.passed for result in budget_results):
        _publish_failed_live_report(
            output_directory=output_directory,
            policy_report=policy_report,
            result_code="M1_RESOURCE_BUDGET_FAILED",
            phases=child.phases,
            workers=child.workers,
            workspaces=child.workspaces,
            failed_evidence=_failed_evidence(
                run_id=run_id,
                failure_stage=FailureStage.BUDGET_EVALUATION,
                started_ns=run_started_ns,
                revision=revision_before,
                tree=tree_before,
                budget_results=budget_results,
            ),
        )
        _fail_after_report(
            "M1_RESOURCE_BUDGET_FAILED",
            "The canonical workload exceeded an enforced resource budget.",
        )
    evidence_payload.update(
        {
            "run_id": str(run_id),
            "sample_count": 1,
            "repository_revision": revision_before,
            "repository_tree": tree_before,
            "repository_worktree_clean": True,
            "report_schema_sha256": _bounded_file_sha256(
                PROJECT_ROOT / "schemas/m1-vertical-slice-benchmark-report-v2.schema.json"
            ),
            "peak_process_tree_rss_bytes": peak_process_tree_rss_bytes,
            "peak_cache_bytes": peak_cache_bytes,
            "peak_temp_bytes": peak_temp_bytes,
            "max_heavy_operation_concurrency": max_heavy_operation_concurrency,
            "cancellation_acknowledgement_ms": cancellation_acknowledgement_ms,
            "low_disk_simulation_status": "passed",
            "slow_io_simulation_status": "passed",
            "worker_recovery_status": "passed",
            "ui_heartbeat_violation_count": int(
                maximum_heartbeat_gap_ns > resource_policy.maximum_ui_heartbeat_gap_ms * 1_000_000
            ),
            "duration_ms": max(1, total_elapsed_ns // 1_000_000),
            "budget_results": [result.to_payload() for result in budget_results],
        }
    )
    completed_evidence = CompletedRunEvidence.from_payload(evidence_payload)
    revision_final, tree_final = _git_identity()
    if revision_final != revision_before or tree_final != tree_before:
        _fail("M1_LIVE_GIT_CHANGED", "The repository changed before live publication.")
    with _open_live_m1_report_writer(output_directory) as writer:
        report = writer._build_completed_report(
            policy_report,
            phase_measurements=child.phases,
            worker_resource_measurements=child.workers,
            workspace_usage_measurements=child.workspaces,
            completed_evidence=completed_evidence,
        )
        return _publish_fixed_bundle(
            output_directory=output_directory,
            derivative=derivative,
            writer=writer,
            report=report,
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--work-directory",
        type=Path,
        default=PROJECT_ROOT / ".paperworkshell-local" / "m1-live-work",
    )
    parser.add_argument("--profile", choices=("constrained",), default="constrained")
    arguments = parser.parse_args(argv)
    try:
        artifacts = run_live_m1_vertical_slice(
            work_directory=cast(Path, arguments.work_directory),
            resource_policy=CONSTRAINED_RESOURCE_POLICY_V1,
        )
    except M1BenchmarkError as error:
        print(f"ERROR [{error.code}] {error.safe_message}", file=sys.stderr)
        return 1
    print("OK: completed the fixed PWS-0114 vertical slice and published local evidence.")
    print(f"Derivative: {artifacts.derivative}")
    print(f"Report: {artifacts.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

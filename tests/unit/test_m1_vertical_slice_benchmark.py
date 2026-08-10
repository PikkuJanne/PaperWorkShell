from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

from tools.m1_vertical_slice_benchmark import (
    EXPECTED_PHASES,
    EXPECTED_VERIFIER_CODES,
    EXPECTED_WORKER_OPERATIONS,
    EXPECTED_WORKSPACE_CHECKPOINTS,
    LIVE_RUNNER_UNAVAILABLE_CODE,
    MAX_REPORT_BYTES,
    MAX_SAFE_INTEGER,
    NOT_RUN_CODE,
    PROJECT_ROOT,
    REPORT_SCHEMA_PATH,
    M1BenchmarkError,
    M1BenchmarkReport,
    M1Phase,
    PhaseMeasurement,
    PhaseMeasurementRecorder,
    PhaseOutcome,
    VerticalSliceMeasurement,
    VerticalSliceRunStatus,
    WorkerOperation,
    WorkerResourceMeasurement,
    WorkspaceCheckpoint,
    WorkspaceUsageMeasurement,
    build_m1_benchmark_report,
    decode_m1_benchmark_report,
    encode_m1_benchmark_report,
    main,
    validate_report_schema,
)
from tools.reference_profile import PROFILE_PATH, ReferenceAttestations, validate_profile

MATCHING_OBSERVATIONS: dict[str, object] = {
    "os_family": "Windows",
    "architecture": "x86_64",
    "logical_processors": 2,
    "memory_bytes": 4 * 1024 * 1024 * 1024,
    "storage_capacity_bytes": 8 * 1024 * 1024 * 1024,
    "storage_free_bytes": 4 * 1024 * 1024,
    "graphics": None,
    "storage_media": None,
}


@dataclass(frozen=True)
class FakeWorkerSnapshot:
    process_memory_limit_bytes: int = 256 * 1024 * 1024
    job_memory_limit_bytes: int = 512 * 1024 * 1024
    peak_process_memory_used_bytes: int = 64 * 1024 * 1024
    peak_job_memory_used_bytes: int = 96 * 1024 * 1024


@dataclass(frozen=True)
class FakeWorkspaceUsage:
    case_bytes: int
    cache_bytes: int
    temp_bytes: int
    entry_count: int
    maximum_depth_seen: int


def _profile() -> dict[str, Any]:
    profile, issues = validate_profile()
    assert not issues
    assert profile is not None
    return profile


def _not_run_measurement() -> VerticalSliceMeasurement:
    return VerticalSliceMeasurement()


def _build(
    tmp_path: Path,
    *,
    observations: dict[str, object] | None = None,
) -> M1BenchmarkReport:
    selected_observations = observations or MATCHING_OBSERVATIONS
    with patch(
        "tools.m1_vertical_slice_benchmark.observe_reference_environment",
        return_value=selected_observations,
    ):
        return build_m1_benchmark_report(
            _profile(),
            "limited-storage",
            tmp_path,
            attestations=ReferenceAttestations(
                memory_mib=4096,
                graphics_class="integrated",
            ),
        )


def _canonical_payload(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            payload, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")
        )
        + "\n"
    ).encode()


def _completed_evidence_payload() -> dict[str, Any]:
    return {
        "run_id": "50000000-0000-4000-8000-000000000114",
        "sample_count": 1,
        "repository_revision": "a" * 40,
        "repository_tree": "b" * 40,
        "repository_worktree_clean": True,
        "reference_profile_sha256": hashlib.sha256(
            (PROJECT_ROOT / PROFILE_PATH).read_bytes()
        ).hexdigest(),
        "report_schema_sha256": hashlib.sha256(
            (PROJECT_ROOT / REPORT_SCHEMA_PATH).read_bytes()
        ).hexdigest(),
        "fixture_set_sha256": "c" * 64,
        "source_set_sha256": "d" * 64,
        "python_version": "3.14.6",
        "pikepdf_version": "10.0.0",
        "qpdf_version": "12.3.2",
        "pdfium_version": "145.0.7632.0",
        "qt_version": "6.11.1",
        "worker_config_sha256": "e" * 64,
        "renderer_profile_sha256": "f" * 64,
        "manifest_schema_version": 1,
        "worker_protocol_version": 1,
        "export_profile_version": 1,
        "workload_version": 1,
        "source_count": 2,
        "source_page_count": 3,
        "active_page_count": 2,
        "preview_count": 3,
        "output_count": 2,
        "output_page_count": 2,
        "output_bytes": 4096,
        "output_sha256": "1" * 64,
        "semantic_fingerprint": "2" * 64,
        "deterministic_semantic_equal": True,
        "deterministic_output_equal": True,
        "verifier_codes": list(EXPECTED_VERIFIER_CODES),
        "recovery_status": "current",
        "case_reopened": True,
        "reopened_page_count": 2,
        "reopened_sequence_equal": True,
        "privacy_scan_status": "passed",
        "privacy_canary_count": 5,
        "background_export_completed": True,
        "heartbeat_count": 10,
        "maximum_heartbeat_gap_ns": 50_000_000,
    }


def _completed_payload(tmp_path: Path) -> dict[str, Any]:
    payload = cast(dict[str, Any], json.loads(encode_m1_benchmark_report(_build(tmp_path))))
    payload["vertical_slice"] = {
        "run_status": "completed",
        "result_code": None,
        "phase_measurements": [
            {"phase": phase.value, "outcome": "completed", "elapsed_ns": index + 1}
            for index, phase in enumerate(EXPECTED_PHASES)
        ],
        "worker_resource_measurements": [
            {
                "operation": operation.value,
                "process_memory_limit_bytes": 256 * 1024 * 1024,
                "job_memory_limit_bytes": 512 * 1024 * 1024,
                "peak_process_memory_used_bytes": 64 * 1024 * 1024,
                "peak_job_memory_used_bytes": 96 * 1024 * 1024,
            }
            for operation in EXPECTED_WORKER_OPERATIONS
        ],
        "workspace_usage_measurements": [
            {
                "checkpoint": checkpoint.value,
                "case_bytes": 10_000 + index,
                "cache_bytes": 2_000,
                "temp_bytes": 1_000,
                "entry_count": 20 + index,
                "maximum_depth_seen": 3,
            }
            for index, checkpoint in enumerate(EXPECTED_WORKSPACE_CHECKPOINTS)
        ],
        "completed_evidence": _completed_evidence_payload(),
    }
    return payload


def _failed_payload(tmp_path: Path) -> dict[str, Any]:
    payload = cast(dict[str, Any], json.loads(encode_m1_benchmark_report(_build(tmp_path))))
    payload["vertical_slice"] = {
        "run_status": "failed",
        "result_code": "M1_IMPORT_FAILED",
        "phase_measurements": [
            {"phase": "case-create", "outcome": "completed", "elapsed_ns": 5},
            {"phase": "pdf-import", "outcome": "failed", "elapsed_ns": 7},
        ],
        "worker_resource_measurements": [
            {
                "operation": "source-inspection",
                "process_memory_limit_bytes": 100,
                "job_memory_limit_bytes": 200,
                "peak_process_memory_used_bytes": 25,
                "peak_job_memory_used_bytes": 50,
            }
        ],
        "workspace_usage_measurements": [
            {
                "checkpoint": "created",
                "case_bytes": 100,
                "cache_bytes": 20,
                "temp_bytes": 10,
                "entry_count": 3,
                "maximum_depth_seen": 2,
            }
        ],
    }
    return payload


def test_checked_in_report_schema_is_valid() -> None:
    assert validate_report_schema() == []


def test_completed_evidence_schema_is_closed_and_every_field_is_required() -> None:
    schema = json.loads((PROJECT_ROOT / REPORT_SCHEMA_PATH).read_bytes())
    completed = schema["$defs"]["completedRunEvidence"]

    assert completed["additionalProperties"] is False
    assert set(completed["required"]) == set(completed["properties"])


def test_not_run_report_is_canonical_closed_and_round_trips(tmp_path: Path) -> None:
    report = _build(tmp_path)

    encoded = encode_m1_benchmark_report(report)
    decoded, issues = decode_m1_benchmark_report(encoded)
    payload = json.loads(encoded)

    assert issues == []
    assert decoded == report
    assert payload["measurement_policy"] == {"product_targets": [], "status": "record-only"}
    assert payload["reference_environment"]["evaluation"]["status"] == "matched"
    assert payload["vertical_slice"] == {
        "phase_measurements": [],
        "result_code": NOT_RUN_CODE,
        "run_status": "not-run",
        "worker_resource_measurements": [],
        "workspace_usage_measurements": [],
    }
    assert "pass" not in encoded.decode().casefold()
    assert "target" not in payload["vertical_slice"]


def test_completed_report_decodes_but_supported_encoder_refuses_it(tmp_path: Path) -> None:
    decoded, issues = decode_m1_benchmark_report(_canonical_payload(_completed_payload(tmp_path)))

    assert issues == []
    assert decoded is not None
    assert decoded.vertical_slice.run_status is VerticalSliceRunStatus.COMPLETED
    assert decoded.vertical_slice.completed_evidence is not None
    assert decoded.vertical_slice.completed_evidence.verifier_codes == EXPECTED_VERIFIER_CODES
    with pytest.raises(M1BenchmarkError) as raised:
        encode_m1_benchmark_report(decoded)
    assert raised.value.code == LIVE_RUNNER_UNAVAILABLE_CODE
    with pytest.raises(M1BenchmarkError) as payload_error:
        decoded.to_payload()
    assert payload_error.value.code == LIVE_RUNNER_UNAVAILABLE_CODE


def test_public_measurement_constructor_can_only_create_not_run() -> None:
    measurement = VerticalSliceMeasurement()

    assert measurement.run_status is VerticalSliceRunStatus.NOT_RUN
    assert measurement.result_code == NOT_RUN_CODE
    with pytest.raises(TypeError):
        VerticalSliceMeasurement(  # type: ignore[call-arg]
            run_status=VerticalSliceRunStatus.COMPLETED
        )


def test_observe_host_cli_emits_only_not_run(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    with patch(
        "tools.m1_vertical_slice_benchmark.observe_reference_environment",
        return_value=MATCHING_OBSERVATIONS,
    ):
        exit_code = main(
            [
                "observe-host",
                "--scenario",
                "limited-storage",
                "--work-directory",
                str(tmp_path),
                "--attest-memory-mib",
                "4096",
                "--attest-graphics",
                "integrated",
            ]
        )

    captured = capfd.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert json.loads(captured.out)["vertical_slice"] == {
        "phase_measurements": [],
        "result_code": NOT_RUN_CODE,
        "run_status": "not-run",
        "worker_resource_measurements": [],
        "workspace_usage_measurements": [],
    }


@pytest.mark.parametrize(
    "field",
    (
        "repository_revision",
        "fixture_set_sha256",
        "worker_config_sha256",
        "output_sha256",
        "semantic_fingerprint",
        "heartbeat_count",
    ),
)
def test_completed_report_requires_identity_and_result_binding(
    tmp_path: Path,
    field: str,
) -> None:
    payload = _completed_payload(tmp_path)
    del payload["vertical_slice"]["completed_evidence"][field]

    decoded, issues = decode_m1_benchmark_report(_canonical_payload(payload))

    assert decoded is None
    assert any(issue.code == "M1_REPORT_SCHEMA_INVALID" for issue in issues)


@pytest.mark.parametrize("field", ("reference_profile_sha256", "report_schema_sha256"))
def test_completed_report_must_match_checked_in_identity_digest(
    tmp_path: Path,
    field: str,
) -> None:
    payload = _completed_payload(tmp_path)
    payload["vertical_slice"]["completed_evidence"][field] = "0" * 64

    decoded, issues = decode_m1_benchmark_report(_canonical_payload(payload))

    assert decoded is None
    assert [issue.code for issue in issues] == ["M1_IDENTITY_INVALID"]


def test_completed_report_requires_exact_seven_verifier_codes(tmp_path: Path) -> None:
    payload = _completed_payload(tmp_path)
    payload["vertical_slice"]["completed_evidence"]["verifier_codes"][-1] = "UNAPPROVED_CHECK"

    decoded, issues = decode_m1_benchmark_report(_canonical_payload(payload))

    assert decoded is None
    assert any(issue.code == "M1_REPORT_SCHEMA_INVALID" for issue in issues)


def test_completed_report_rejects_cross_result_count_mismatch(tmp_path: Path) -> None:
    payload = _completed_payload(tmp_path)
    payload["vertical_slice"]["completed_evidence"]["reopened_page_count"] = 1

    decoded, issues = decode_m1_benchmark_report(_canonical_payload(payload))

    assert decoded is None
    assert [issue.code for issue in issues] == ["M1_MEASUREMENT_INVALID"]


def test_overpowered_current_style_host_is_recorded_as_mismatched(tmp_path: Path) -> None:
    observations = dict(MATCHING_OBSERVATIONS)
    observations["logical_processors"] = 8
    observations["storage_capacity_bytes"] = 980_000_000_000

    report = _build(tmp_path, observations=observations)
    payload = json.loads(encode_m1_benchmark_report(report))
    evaluation = payload["reference_environment"]["evaluation"]

    assert evaluation["status"] == "mismatched"
    assert evaluation["requirements"]["logical_processors"]["status"] == "mismatched"
    assert evaluation["requirements"]["storage_capacity_bytes"]["status"] == "mismatched"
    assert payload["vertical_slice"]["run_status"] == "not-run"


def test_phase_recorder_uses_advancing_perf_counter_values() -> None:
    readings = iter((10, 35))
    recorder = PhaseMeasurementRecorder(lambda: next(readings))

    with recorder.measure(M1Phase.CASE_CREATE):
        pass

    assert recorder.measurements == (
        PhaseMeasurement(M1Phase.CASE_CREATE, PhaseOutcome.COMPLETED, 25),
    )


def test_phase_recorder_bounds_elapsed_value_not_absolute_uptime() -> None:
    readings = iter((MAX_SAFE_INTEGER + 100, MAX_SAFE_INTEGER + 125))
    recorder = PhaseMeasurementRecorder(lambda: next(readings))

    with recorder.measure(M1Phase.CASE_CREATE):
        pass

    assert recorder.measurements[0].elapsed_ns == 25


def test_phase_recorder_records_failure_without_exception_text() -> None:
    readings = iter((100, 145))
    recorder = PhaseMeasurementRecorder(lambda: next(readings))

    with pytest.raises(RuntimeError, match="private sample name"):
        with recorder.measure(M1Phase.CASE_CREATE):
            raise RuntimeError("private sample name")

    assert recorder.measurements == (
        PhaseMeasurement(M1Phase.CASE_CREATE, PhaseOutcome.FAILED, 45),
    )
    assert "private sample name" not in repr(recorder.measurements)


def test_phase_recorder_rejects_duplicate_or_nonadvancing_measurements() -> None:
    recorder = PhaseMeasurementRecorder(lambda: 10)

    with pytest.raises(M1BenchmarkError, match="did not advance"):
        with recorder.measure(M1Phase.CASE_CREATE):
            pass

    readings = iter((1, 2))
    ordered = PhaseMeasurementRecorder(lambda: next(readings))
    with ordered.measure(M1Phase.CASE_CREATE):
        pass
    with pytest.raises(M1BenchmarkError, match="exact vertical-slice prefix"):
        with ordered.measure(M1Phase.CASE_CREATE):
            pass


def test_completed_status_rejects_missing_measurement_categories(tmp_path: Path) -> None:
    payload = _completed_payload(tmp_path)
    payload["vertical_slice"]["worker_resource_measurements"] = []

    decoded, issues = decode_m1_benchmark_report(_canonical_payload(payload))

    assert decoded is None
    assert any(issue.code == "M1_REPORT_SCHEMA_INVALID" for issue in issues)


def test_completed_status_requires_completed_evidence(tmp_path: Path) -> None:
    payload = _completed_payload(tmp_path)
    del payload["vertical_slice"]["completed_evidence"]

    decoded, issues = decode_m1_benchmark_report(_canonical_payload(payload))

    assert decoded is None
    assert any(issue.code == "M1_REPORT_SCHEMA_INVALID" for issue in issues)


def test_non_run_status_rejects_completed_evidence(tmp_path: Path) -> None:
    payload = json.loads(encode_m1_benchmark_report(_build(tmp_path)))
    payload["vertical_slice"]["completed_evidence"] = _completed_evidence_payload()

    decoded, issues = decode_m1_benchmark_report(_canonical_payload(payload))

    assert decoded is None
    assert any(issue.code == "M1_REPORT_SCHEMA_INVALID" for issue in issues)


def test_non_run_status_rejects_workload_measurements(tmp_path: Path) -> None:
    payload = json.loads(encode_m1_benchmark_report(_build(tmp_path)))
    payload["vertical_slice"]["phase_measurements"] = [
        {"phase": "case-create", "outcome": "completed", "elapsed_ns": 1}
    ]

    decoded, issues = decode_m1_benchmark_report(_canonical_payload(payload))

    assert decoded is None
    assert any(issue.code == "M1_REPORT_SCHEMA_INVALID" for issue in issues)


@pytest.mark.parametrize(
    ("process_peak", "job_peak"),
    ((0, 1), (101, 101), (50, 49)),
)
def test_worker_snapshot_rejects_impossible_peak_values(
    process_peak: int,
    job_peak: int,
) -> None:
    snapshot = FakeWorkerSnapshot(
        process_memory_limit_bytes=100,
        job_memory_limit_bytes=100,
        peak_process_memory_used_bytes=process_peak,
        peak_job_memory_used_bytes=job_peak,
    )

    with pytest.raises(M1BenchmarkError):
        WorkerResourceMeasurement.from_snapshot(WorkerOperation.VERIFIED_EXPORT, snapshot)


def test_workspace_snapshot_rejects_area_bytes_above_case_total() -> None:
    usage = FakeWorkspaceUsage(100, 80, 21, 1, 1)

    with pytest.raises(M1BenchmarkError, match="cannot exceed"):
        WorkspaceUsageMeasurement.from_usage(WorkspaceCheckpoint.EXPORTED, usage)


def test_codec_rejects_tampered_reference_evaluation(tmp_path: Path) -> None:
    payload = json.loads(encode_m1_benchmark_report(_build(tmp_path)))
    payload["reference_environment"]["evaluation"]["status"] = "mismatched"

    decoded, issues = decode_m1_benchmark_report(_canonical_payload(payload))

    assert decoded is None
    assert any(issue.code == "M1_REFERENCE_INVALID" for issue in issues)


@pytest.mark.parametrize(
    ("payload", "code"),
    (
        (b'{"report_schema_version":1,"report_schema_version":1}', "M1_REPORT_DUPLICATE_KEY"),
        (b'{"report_schema_version":NaN}', "M1_REPORT_NONSTANDARD_NUMBER"),
        (b"[]", "M1_REPORT_ROOT_INVALID"),
        (b"\xff", "M1_REPORT_ENCODING_INVALID"),
    ),
)
def test_codec_rejects_noncanonical_json(payload: bytes, code: str) -> None:
    decoded, issues = decode_m1_benchmark_report(payload)

    assert decoded is None
    assert [issue.code for issue in issues] == [code]


def test_codec_rejects_valid_json_that_is_not_canonical(tmp_path: Path) -> None:
    payload = json.loads(encode_m1_benchmark_report(_build(tmp_path)))
    noncanonical = json.dumps(payload, indent=2, sort_keys=False).encode("utf-8")

    decoded, issues = decode_m1_benchmark_report(noncanonical)

    assert decoded is None
    assert [issue.code for issue in issues] == ["M1_REPORT_NONCANONICAL"]


def test_codec_rejects_oversized_report_without_parsing() -> None:
    decoded, issues = decode_m1_benchmark_report(b"{" + b" " * MAX_REPORT_BYTES)

    assert decoded is None
    assert [issue.code for issue in issues] == ["M1_REPORT_SIZE_INVALID"]


def test_codec_rejects_unknown_future_schema_version(tmp_path: Path) -> None:
    payload = json.loads(encode_m1_benchmark_report(_build(tmp_path)))
    payload["report_schema_version"] = 2

    decoded, issues = decode_m1_benchmark_report(_canonical_payload(payload))

    assert decoded is None
    assert any(issue.code == "M1_REPORT_SCHEMA_INVALID" for issue in issues)


def test_report_schema_validation_fails_closed_when_schema_is_missing(tmp_path: Path) -> None:
    issues = validate_report_schema(tmp_path)

    assert [issue.code for issue in issues] == ["M1_REPORT_SCHEMA_UNREADABLE"]


def test_schema_rejects_integer_above_json_safe_range(tmp_path: Path) -> None:
    payload = json.loads(encode_m1_benchmark_report(_build(tmp_path)))
    payload["reference_environment"]["observations"]["storage_capacity_bytes"] = (
        MAX_SAFE_INTEGER + 1
    )

    decoded, issues = decode_m1_benchmark_report(_canonical_payload(payload))

    assert decoded is None
    assert any(issue.code == "M1_REPORT_SCHEMA_INVALID" for issue in issues)


def test_measurement_rejects_integer_above_json_safe_range() -> None:
    snapshot = FakeWorkerSnapshot(
        process_memory_limit_bytes=MAX_SAFE_INTEGER + 1,
        job_memory_limit_bytes=MAX_SAFE_INTEGER + 1,
    )

    with pytest.raises(M1BenchmarkError, match=str(MAX_SAFE_INTEGER)):
        WorkerResourceMeasurement.from_snapshot(WorkerOperation.VERIFIED_EXPORT, snapshot)


def test_failed_measurement_accepts_only_a_causal_prefix(tmp_path: Path) -> None:
    decoded, issues = decode_m1_benchmark_report(_canonical_payload(_failed_payload(tmp_path)))

    assert issues == []
    assert decoded is not None
    assert decoded.vertical_slice.run_status is VerticalSliceRunStatus.FAILED
    assert decoded.vertical_slice.result_code == "M1_IMPORT_FAILED"
    with pytest.raises(M1BenchmarkError) as raised:
        encode_m1_benchmark_report(decoded)
    assert raised.value.code == LIVE_RUNNER_UNAVAILABLE_CODE
    with pytest.raises(M1BenchmarkError) as payload_error:
        decoded.to_payload()
    assert payload_error.value.code == LIVE_RUNNER_UNAVAILABLE_CODE


def test_failed_measurement_rejects_completed_evidence(tmp_path: Path) -> None:
    payload = _failed_payload(tmp_path)
    payload["vertical_slice"]["completed_evidence"] = _completed_evidence_payload()

    decoded, issues = decode_m1_benchmark_report(_canonical_payload(payload))

    assert decoded is None
    assert any(issue.code == "M1_REPORT_SCHEMA_INVALID" for issue in issues)


def test_failed_measurement_rejects_noncontiguous_phase_prefix(tmp_path: Path) -> None:
    payload = _failed_payload(tmp_path)
    payload["vertical_slice"]["phase_measurements"] = [
        {"phase": "pdf-import", "outcome": "failed", "elapsed_ns": 7}
    ]
    payload["vertical_slice"]["worker_resource_measurements"] = []
    payload["vertical_slice"]["workspace_usage_measurements"] = []

    decoded, issues = decode_m1_benchmark_report(_canonical_payload(payload))

    assert decoded is None
    assert [issue.code for issue in issues] == ["M1_MEASUREMENT_INVALID"]


@pytest.mark.parametrize("causal_kind", ("worker", "workspace"))
def test_failed_measurement_rejects_evidence_after_failure(
    tmp_path: Path,
    causal_kind: str,
) -> None:
    payload = _failed_payload(tmp_path)
    if causal_kind == "worker":
        payload["vertical_slice"]["worker_resource_measurements"].append(
            {
                "operation": "verified-export",
                "process_memory_limit_bytes": 100,
                "job_memory_limit_bytes": 200,
                "peak_process_memory_used_bytes": 25,
                "peak_job_memory_used_bytes": 50,
            }
        )
    else:
        payload["vertical_slice"]["workspace_usage_measurements"].append(
            {
                "checkpoint": "recovered",
                "case_bytes": 100,
                "cache_bytes": 20,
                "temp_bytes": 10,
                "entry_count": 3,
                "maximum_depth_seen": 2,
            }
        )

    decoded, issues = decode_m1_benchmark_report(_canonical_payload(payload))

    assert decoded is None
    assert [issue.code for issue in issues] == ["M1_MEASUREMENT_INVALID"]


def test_failed_measurement_rejects_completion_after_failure(tmp_path: Path) -> None:
    payload = _failed_payload(tmp_path)
    payload["vertical_slice"]["phase_measurements"] = [
        {"phase": "case-create", "outcome": "failed", "elapsed_ns": 5},
        {"phase": "pdf-import", "outcome": "completed", "elapsed_ns": 7},
    ]
    payload["vertical_slice"]["worker_resource_measurements"] = []
    payload["vertical_slice"]["workspace_usage_measurements"] = []

    decoded, issues = decode_m1_benchmark_report(_canonical_payload(payload))

    assert decoded is None
    assert [issue.code for issue in issues] == ["M1_MEASUREMENT_INVALID"]

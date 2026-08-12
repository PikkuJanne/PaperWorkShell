from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import pytest

from paperworkshell.domain.resource_policy import (
    CONSTRAINED_RESOURCE_POLICY_V1,
    NORMAL_RESOURCE_POLICY_V1,
)
from paperworkshell.storage._windows import WindowsHandle
from tools.m1_vertical_slice_benchmark import (
    _LIVE_M1_REPORT_AUTHORITY_MARKER,
    HARDWARE_CLAIM_LIMITATION,
    LIVE_REPORT_ALREADY_EXISTS_CODE,
    LIVE_REPORT_FILENAME,
    MEASUREMENT_LIMITATION,
    REPORT_SCHEMA_PATH,
    BudgetId,
    BudgetResult,
    CompletedRunEvidence,
    FailedRunEvidence,
    FailureStage,
    HistoricalM1BenchmarkReport,
    M1BenchmarkError,
    M1BenchmarkReport,
    M1Phase,
    PhaseMeasurement,
    PhaseMeasurementRecorder,
    PhaseOutcome,
    VerticalSliceRunStatus,
    WorkerOperation,
    WorkerResourceMeasurement,
    WorkspaceCheckpoint,
    WorkspaceUsageMeasurement,
    _build_live_m1_completed_report,
    _build_live_m1_failed_report,
    _encode_live_m1_completed_report,
    _encode_live_m1_failed_report,
    _LiveM1ReportAuthority,
    _LiveM1ReportWriter,
    _open_live_m1_report_writer,
    build_m1_benchmark_report,
    decode_m1_benchmark_report,
    encode_m1_benchmark_report,
    validate_report_schema,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _phases() -> tuple[PhaseMeasurement, ...]:
    return tuple(PhaseMeasurement(phase, PhaseOutcome.COMPLETED, 10) for phase in M1Phase)


def _workers() -> tuple[WorkerResourceMeasurement, ...]:
    return tuple(
        WorkerResourceMeasurement(operation, 100, 200, 25, 50) for operation in WorkerOperation
    )


def _workspaces() -> tuple[WorkspaceUsageMeasurement, ...]:
    return tuple(
        WorkspaceUsageMeasurement(checkpoint, 100, 20, 10, 3, 2)
        for checkpoint in WorkspaceCheckpoint
    )


def _budget_results() -> tuple[BudgetResult, ...]:
    policy = CONSTRAINED_RESOURCE_POLICY_V1
    return (
        BudgetResult(BudgetId.PROCESS_TREE_RSS, 100, policy.canonical_process_tree_rss_bytes, True),
        BudgetResult(
            BudgetId.CACHE_BYTES,
            20,
            policy.maximum_cache_bytes + policy.thumbnail_preview_cache_bytes,
            True,
        ),
        BudgetResult(BudgetId.TEMP_BYTES, 10, policy.maximum_temporary_storage_bytes, True),
        BudgetResult(
            BudgetId.HEAVY_CONCURRENCY, 1, policy.maximum_heavy_operation_concurrency, True
        ),
        BudgetResult(BudgetId.UI_HEARTBEAT, 50, policy.maximum_ui_heartbeat_gap_ms, True),
        BudgetResult(
            BudgetId.CANCELLATION_ACKNOWLEDGEMENT,
            100,
            policy.cancellation_acknowledgement_ms,
            True,
        ),
    )


def _evidence() -> CompletedRunEvidence:
    schema_digest = hashlib.sha256((PROJECT_ROOT / REPORT_SCHEMA_PATH).read_bytes()).hexdigest()
    return CompletedRunEvidence(
        run_id=UUID("12345678-1234-4234-9234-123456789abc"),
        sample_count=1,
        total_elapsed_ns=100_000_000,
        repository_revision="a" * 40,
        repository_tree="b" * 40,
        repository_worktree_clean=True,
        report_schema_sha256=schema_digest,
        fixture_set_sha256="c" * 64,
        source_set_sha256="d" * 64,
        python_version="3.14.0",
        pikepdf_version="10.0.0",
        qpdf_version="12.3.2",
        pdfium_version="1.0",
        qt_version="6.9.0",
        worker_config_sha256="e" * 64,
        renderer_profile_sha256="f" * 64,
        manifest_schema_version=1,
        worker_protocol_version=1,
        export_profile_version=1,
        workload_version=1,
        source_count=2,
        source_page_count=3,
        active_page_count=2,
        preview_count=2,
        output_count=2,
        output_page_count=2,
        output_bytes=100,
        output_sha256="1" * 64,
        semantic_fingerprint="2" * 64,
        deterministic_semantic_equal=True,
        deterministic_output_equal=True,
        verifier_codes=(
            "PDF_REOPENED",
            "STRUCTURE_CLEAN",
            "PAGE_COUNT_MATCHED",
            "UNENCRYPTED",
            "ALL_PAGES_RENDERED",
            "ORDER_ROTATION_MATCHED",
            "OUTPUT_COMMIT_REOPENED",
        ),
        recovery_status="recovered",
        case_reopened=True,
        reopened_page_count=2,
        reopened_sequence_equal=True,
        privacy_scan_status="passed",
        privacy_canary_count=5,
        background_export_completed=True,
        workers_terminated=True,
        worker_recycle_policy="one-shot",
        low_disk_simulation_status="passed",
        slow_io_simulation_status="passed",
        worker_recovery_status="passed",
        heartbeat_count=10,
        maximum_heartbeat_gap_ns=50_000_000,
        peak_process_tree_rss_bytes=100,
        peak_cache_bytes=20,
        peak_temp_bytes=10,
        max_heavy_operation_concurrency=1,
        cancellation_acknowledgement_ms=100,
        ui_heartbeat_violation_count=0,
        duration_ms=100,
        input_bytes=200,
        budget_results=_budget_results(),
    )


def _completed_payload() -> bytes:
    authority = _LiveM1ReportAuthority(_LIVE_M1_REPORT_AUTHORITY_MARKER)
    report = _build_live_m1_completed_report(
        authority,
        build_m1_benchmark_report(CONSTRAINED_RESOURCE_POLICY_V1),
        phase_measurements=_phases(),
        worker_resource_measurements=_workers(),
        workspace_usage_measurements=_workspaces(),
        completed_evidence=_evidence(),
    )
    return _encode_live_m1_completed_report(authority, report)


def _failed_evidence(
    stage: FailureStage,
    *,
    budget_results: tuple[BudgetResult, ...] = (),
) -> FailedRunEvidence:
    schema_digest = hashlib.sha256((PROJECT_ROOT / REPORT_SCHEMA_PATH).read_bytes()).hexdigest()
    return FailedRunEvidence(
        run_id=UUID("abcdefab-1234-4234-9234-abcdefabcdef"),
        failure_stage=stage,
        total_elapsed_ns=100_000_000,
        repository_revision="a" * 40,
        repository_tree="b" * 40,
        repository_worktree_clean=True,
        report_schema_sha256=schema_digest,
        budget_results=budget_results,
    )


def _failed_report(
    stage: FailureStage,
    *,
    result_code: str = "M1_LIVE_CHILD_FAILED",
    phases: tuple[PhaseMeasurement, ...] = (),
    workers: tuple[WorkerResourceMeasurement, ...] = (),
    workspaces: tuple[WorkspaceUsageMeasurement, ...] = (),
    budget_results: tuple[BudgetResult, ...] = (),
) -> M1BenchmarkReport:
    authority = _LiveM1ReportAuthority(_LIVE_M1_REPORT_AUTHORITY_MARKER)
    return _build_live_m1_failed_report(
        authority,
        build_m1_benchmark_report(CONSTRAINED_RESOURCE_POLICY_V1),
        result_code=result_code,
        phase_measurements=phases,
        worker_resource_measurements=workers,
        workspace_usage_measurements=workspaces,
        failed_evidence=_failed_evidence(stage, budget_results=budget_results),
    )


def _failed_payload(
    stage: FailureStage,
    *,
    result_code: str = "M1_LIVE_CHILD_FAILED",
    phases: tuple[PhaseMeasurement, ...] = (),
    workers: tuple[WorkerResourceMeasurement, ...] = (),
    workspaces: tuple[WorkspaceUsageMeasurement, ...] = (),
    budget_results: tuple[BudgetResult, ...] = (),
) -> bytes:
    authority = _LiveM1ReportAuthority(_LIVE_M1_REPORT_AUTHORITY_MARKER)
    return _encode_live_m1_failed_report(
        authority,
        _failed_report(
            stage,
            result_code=result_code,
            phases=phases,
            workers=workers,
            workspaces=workspaces,
            budget_results=budget_results,
        ),
    )


def test_constrained_policy_report_is_closed_and_contains_no_hardware_observation() -> None:
    payload = json.loads(
        encode_m1_benchmark_report(build_m1_benchmark_report(CONSTRAINED_RESOURCE_POLICY_V1))
    )
    assert payload["schema_version"] == 2
    assert payload["processing_profile"] == "constrained"
    assert payload["resource_policy_version"] == 1
    assert payload["resource_limits"]["canonical_process_tree_rss_bytes"] == 1_280 * 1024 * 1024
    assert payload["limitations"] == [HARDWARE_CLAIM_LIMITATION, MEASUREMENT_LIMITATION]
    rendered = json.dumps(payload).lower()
    for forbidden in (
        "reference_environment",
        "attestation",
        "graphics",
        "storage_media",
        "scenario_id",
    ):
        assert forbidden not in rendered


def test_profile_selection_changes_enforced_limits_not_only_label() -> None:
    constrained = build_m1_benchmark_report(CONSTRAINED_RESOURCE_POLICY_V1)
    normal = build_m1_benchmark_report(NORMAL_RESOURCE_POLICY_V1)
    assert constrained.processing_profile != normal.processing_profile
    assert constrained.resource_limits != normal.resource_limits
    assert constrained.resource_limits["maximum_cache_bytes"] == 64 * 1024 * 1024
    assert constrained.resource_limits["thumbnail_preview_cache_bytes"] == 64 * 1024 * 1024


def test_report_serializes_every_numeric_resource_policy_control() -> None:
    report = build_m1_benchmark_report(CONSTRAINED_RESOURCE_POLICY_V1)
    expected = set(CONSTRAINED_RESOURCE_POLICY_V1.to_evidence()) - {"profile", "policy_version"}
    assert set(report.resource_limits) == expected


def test_schema_and_non_run_round_trip() -> None:
    assert validate_report_schema() == []
    encoded = encode_m1_benchmark_report(build_m1_benchmark_report(CONSTRAINED_RESOURCE_POLICY_V1))
    decoded, issues = decode_m1_benchmark_report(encoded)
    assert issues == []
    assert type(decoded) is M1BenchmarkReport
    assert decoded.vertical_slice.run_status is VerticalSliceRunStatus.NOT_RUN


def test_completed_report_round_trip_binds_hard_budget_results() -> None:
    encoded = _completed_payload()
    decoded, issues = decode_m1_benchmark_report(encoded)
    assert issues == []
    assert type(decoded) is M1BenchmarkReport
    evidence = decoded.vertical_slice.completed_evidence
    assert evidence is not None
    assert evidence.peak_process_tree_rss_bytes == 100
    assert tuple(item.budget_id for item in evidence.budget_results) == tuple(BudgetId)
    assert all(item.passed for item in evidence.budget_results)
    assert evidence.cancellation_acknowledgement_ms == 100
    assert evidence.workers_terminated is True
    assert evidence.worker_recycle_policy == "one-shot"


def test_failed_setup_report_round_trip_does_not_fabricate_phases() -> None:
    encoded = _failed_payload(FailureStage.SETUP)
    decoded, issues = decode_m1_benchmark_report(encoded)
    assert issues == []
    assert type(decoded) is M1BenchmarkReport
    assert decoded.vertical_slice.run_status is VerticalSliceRunStatus.FAILED
    assert decoded.vertical_slice.phase_measurements == ()
    assert decoded.vertical_slice.failed_evidence == _failed_evidence(FailureStage.SETUP)


def test_failed_workload_report_preserves_only_available_prefix_evidence() -> None:
    phases = (
        PhaseMeasurement(M1Phase.CASE_CREATE, PhaseOutcome.COMPLETED, 10),
        PhaseMeasurement(M1Phase.PDF_IMPORT, PhaseOutcome.FAILED, 20),
    )
    workers = (_workers()[0],)
    workspaces = (_workspaces()[0],)
    encoded = _failed_payload(
        FailureStage.WORKLOAD,
        phases=phases,
        workers=workers,
        workspaces=workspaces,
    )
    decoded, issues = decode_m1_benchmark_report(encoded)
    assert issues == []
    assert type(decoded) is M1BenchmarkReport
    assert decoded.vertical_slice.phase_measurements == phases
    assert decoded.vertical_slice.worker_resource_measurements == workers
    assert decoded.vertical_slice.workspace_usage_measurements == workspaces


def test_failed_post_workload_budget_report_keeps_completed_phases_and_failed_budgets() -> None:
    budget_results = list(_budget_results())
    rss = budget_results[0]
    budget_results[0] = BudgetResult(rss.budget_id, rss.limit + 1, rss.limit, False)
    encoded = _failed_payload(
        FailureStage.BUDGET_EVALUATION,
        result_code="M1_RESOURCE_BUDGET_FAILED",
        phases=_phases(),
        workers=_workers(),
        workspaces=_workspaces(),
        budget_results=tuple(budget_results),
    )
    decoded, issues = decode_m1_benchmark_report(encoded)
    assert issues == []
    assert type(decoded) is M1BenchmarkReport
    assert all(
        item.outcome is PhaseOutcome.COMPLETED for item in decoded.vertical_slice.phase_measurements
    )
    assert decoded.vertical_slice.completed_evidence is None
    assert decoded.vertical_slice.failed_evidence is not None
    assert any(not item.passed for item in decoded.vertical_slice.failed_evidence.budget_results)


def test_post_workload_correctness_failure_does_not_relabel_completed_phase() -> None:
    encoded = _failed_payload(
        FailureStage.WORKLOAD,
        result_code="M1_OUTPUT_CORRECTNESS_FAILED",
        phases=_phases(),
        workers=_workers(),
        workspaces=_workspaces(),
    )
    decoded, issues = decode_m1_benchmark_report(encoded)
    assert issues == []
    assert type(decoded) is M1BenchmarkReport
    assert all(
        item.outcome is PhaseOutcome.COMPLETED for item in decoded.vertical_slice.phase_measurements
    )


def test_budget_failure_rejects_falsely_failed_completed_workload_phase() -> None:
    budget_results = list(_budget_results())
    cancellation = budget_results[-1]
    budget_results[-1] = BudgetResult(
        cancellation.budget_id,
        cancellation.limit + 1,
        cancellation.limit,
        False,
    )
    phases = (
        *_phases()[:-1],
        PhaseMeasurement(M1Phase.CASE_REOPEN_RECOVERY, PhaseOutcome.FAILED, 10),
    )
    with pytest.raises(M1BenchmarkError, match="complete measured workload"):
        _failed_report(
            FailureStage.BUDGET_EVALUATION,
            result_code="M1_RESOURCE_BUDGET_FAILED",
            phases=phases,
            workers=_workers(),
            workspaces=_workspaces(),
            budget_results=tuple(budget_results),
        )


@pytest.mark.parametrize(
    ("mutator", "expected_code"),
    (
        (lambda value: value.update({"schema_version": 3}), "M1_REPORT_VERSION_UNSUPPORTED"),
        (
            lambda value: value["resource_limits"].update({"thumbnail_preview_cache_bytes": 1}),
            "M1_RESOURCE_POLICY_INVALID",
        ),
        (
            lambda value: value["vertical_slice"]["completed_evidence"].update(
                {"peak_cache_bytes": 21}
            ),
            "M1_RESOURCE_BUDGET_INVALID",
        ),
    ),
)
def test_decode_rejects_future_or_tampered_resource_evidence(
    mutator: Callable[[dict[str, object]], object],
    expected_code: str,
) -> None:
    value = json.loads(_completed_payload())
    mutator(value)
    payload = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    decoded, issues = decode_m1_benchmark_report(payload)
    assert decoded is None
    assert issues[0].code == expected_code


@pytest.mark.parametrize(
    ("payload", "code"),
    (
        (b'{"schema_version":1,"schema_version":1}\n', "M1_REPORT_DUPLICATE_KEY"),
        (b'{"schema_version":NaN}\n', "M1_REPORT_NONSTANDARD_NUMBER"),
        (b"\xff", "M1_REPORT_ENCODING_INVALID"),
    ),
)
def test_decoder_rejects_ambiguous_or_invalid_json(payload: bytes, code: str) -> None:
    decoded, issues = decode_m1_benchmark_report(payload)
    assert decoded is None
    assert issues[0].code == code


def _historical_v1_payload() -> bytes:
    value = {
        "report_schema_version": 1,
        "report_kind": "m1-vertical-slice-measurement",
        "profile_id": "reference-windows-v1",
        "scenario_id": "local-smoke",
        "measurement_policy": {"status": "record-only", "product_targets": []},
        "reference_environment": {
            "observations": {
                "os_family": "Windows",
                "architecture": "AMD64",
                "logical_processors": None,
                "memory_bytes": None,
                "storage_capacity_bytes": 0,
                "storage_free_bytes": 0,
                "graphics": None,
                "storage_media": None,
            },
            "attestations": {
                "memory_mib": None,
                "graphics_class": None,
                "storage_media": None,
            },
            "evaluation": {"status": "not-evaluated", "requirements": {}},
        },
        "vertical_slice": {
            "run_status": "not-run",
            "result_code": "M1_VERTICAL_SLICE_NOT_RUN",
            "phase_measurements": [],
            "worker_resource_measurements": [],
            "workspace_usage_measurements": [],
        },
    }
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def test_decoder_retains_historical_v1_read_support() -> None:
    decoded, issues = decode_m1_benchmark_report(_historical_v1_payload())
    assert issues == []
    assert type(decoded) is HistoricalM1BenchmarkReport
    assert decoded.run_status == "not-run"


def test_historical_v1_decoder_rejects_invalid_instance() -> None:
    value = json.loads(_historical_v1_payload())
    value["reference_environment"]["attestations"]["memory_mib"] = 0
    payload = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    decoded, issues = decode_m1_benchmark_report(payload)
    assert decoded is None
    assert issues[0].code == "M1_REPORT_SCHEMA_INVALID"


def test_budget_result_cannot_lie_about_failure() -> None:
    with pytest.raises(M1BenchmarkError, match="does not match"):
        BudgetResult(BudgetId.CACHE_BYTES, 11, 10, True)
    assert BudgetResult(BudgetId.CACHE_BYTES, 11, 10, False).passed is False


@pytest.mark.parametrize("budget_id", tuple(BudgetId))
def test_completed_evidence_rejects_each_failed_hard_budget(budget_id: BudgetId) -> None:
    results = list(_budget_results())
    index = tuple(BudgetId).index(budget_id)
    original = results[index]
    results[index] = BudgetResult(budget_id, original.limit + 1, original.limit, False)
    with pytest.raises(M1BenchmarkError) as raised:
        replace(_evidence(), budget_results=tuple(results))
    assert raised.value.code == "M1_RESOURCE_BUDGET_FAILED"


def test_phase_recorder_uses_monotonic_measurements_and_order() -> None:
    ticks = iter((10, 20, 30, 50))
    recorder = PhaseMeasurementRecorder(lambda: next(ticks))
    with recorder.measure(M1Phase.CASE_CREATE):
        pass
    with recorder.measure(M1Phase.PDF_IMPORT):
        pass
    assert [item.elapsed_ns for item in recorder.measurements] == [10, 20]


@dataclass(frozen=True)
class _WorkerSnapshot:
    process_memory_limit_bytes: int = 100
    job_memory_limit_bytes: int = 200
    peak_process_memory_used_bytes: int = 25
    peak_job_memory_used_bytes: int = 50


def test_worker_measurement_copies_only_sanitized_numeric_snapshot_fields() -> None:
    measurement = WorkerResourceMeasurement.from_snapshot(
        WorkerOperation.SOURCE_INSPECTION,
        _WorkerSnapshot(),
    )
    assert measurement.peak_job_memory_used_bytes == 50


def test_public_encoder_refuses_completed_authority() -> None:
    authority = _LiveM1ReportAuthority(_LIVE_M1_REPORT_AUTHORITY_MARKER)
    report = _build_live_m1_completed_report(
        authority,
        build_m1_benchmark_report(CONSTRAINED_RESOURCE_POLICY_V1),
        phase_measurements=_phases(),
        worker_resource_measurements=_workers(),
        workspace_usage_measurements=_workspaces(),
        completed_evidence=_evidence(),
    )
    with pytest.raises(M1BenchmarkError) as raised:
        encode_m1_benchmark_report(report)
    assert raised.value.code == "M1_LIVE_RUNNER_UNAVAILABLE"


def test_public_encoder_refuses_failed_authority() -> None:
    with pytest.raises(M1BenchmarkError) as raised:
        encode_m1_benchmark_report(_failed_report(FailureStage.SETUP))
    assert raised.value.code == "M1_LIVE_RUNNER_UNAVAILABLE"


def test_private_failed_writer_atomically_publishes_only_canonical_report(tmp_path: Path) -> None:
    with _open_live_m1_report_writer(tmp_path) as writer:
        report = writer._build_failed_report(
            build_m1_benchmark_report(CONSTRAINED_RESOURCE_POLICY_V1),
            result_code="M1_LIVE_CHILD_FAILED",
            phase_measurements=(),
            worker_resource_measurements=(),
            workspace_usage_measurements=(),
            failed_evidence=_failed_evidence(FailureStage.SETUP),
        )
        destination = writer._publish_failed_report(report)

    assert destination == tmp_path / LIVE_REPORT_FILENAME
    assert {item.name for item in tmp_path.iterdir()} == {LIVE_REPORT_FILENAME}
    decoded, issues = decode_m1_benchmark_report(destination.read_bytes())
    assert issues == []
    assert type(decoded) is M1BenchmarkReport
    assert decoded.vertical_slice.run_status is VerticalSliceRunStatus.FAILED


def test_private_failed_writer_preserves_existing_fixed_destination(tmp_path: Path) -> None:
    destination = tmp_path / LIVE_REPORT_FILENAME
    original = b"preexisting-report"
    destination.write_bytes(original)
    with (
        _open_live_m1_report_writer(tmp_path) as writer,
        pytest.raises(M1BenchmarkError) as raised,
    ):
        writer._publish_failed_report(_failed_report(FailureStage.SETUP))

    assert raised.value.code == LIVE_REPORT_ALREADY_EXISTS_CODE
    assert destination.read_bytes() == original
    assert {item.name for item in tmp_path.iterdir()} == {LIVE_REPORT_FILENAME}


def test_private_failed_writer_interrupt_before_commit_discards_stage(tmp_path: Path) -> None:
    with (
        _open_live_m1_report_writer(tmp_path) as writer,
        patch(
            "tools.m1_vertical_slice_benchmark._validate_authenticated_live_report",
            side_effect=KeyboardInterrupt,
        ),
        pytest.raises(KeyboardInterrupt),
    ):
        writer._publish_failed_report(_failed_report(FailureStage.SETUP))

    assert list(tmp_path.iterdir()) == []


def test_private_failed_writer_interrupt_after_commit_preserves_report(tmp_path: Path) -> None:
    def interrupt_after_commit(
        _writer: _LiveM1ReportWriter,
        handle: WindowsHandle,
    ) -> None:
        handle.close()
        raise KeyboardInterrupt

    with _open_live_m1_report_writer(tmp_path) as writer:
        report = _failed_report(FailureStage.SETUP)
        with (
            patch.object(
                _LiveM1ReportWriter,
                "_settle_committed_stage_handle",
                new=interrupt_after_commit,
            ),
            pytest.raises(KeyboardInterrupt),
        ):
            writer._publish_failed_report(report)
        assert writer._published is True

    destination = tmp_path / LIVE_REPORT_FILENAME
    decoded, issues = decode_m1_benchmark_report(destination.read_bytes())
    assert issues == []
    assert decoded == report

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.pws0208_resource_evidence import (
    PWS0208_MAXIMUM_CANCELLATION_ACKNOWLEDGEMENT_MS,
    Pws0208ResourceEvidence,
    decode_pws0208_resource_evidence,
    encode_pws0208_resource_evidence,
    validate_report,
    validate_workload,
)


def _evidence(**changes: object) -> Pws0208ResourceEvidence:
    values: dict[str, object] = {
        "revision": "a" * 40,
        "input_bytes": 1_000,
        "output_bytes": 2_000,
        "output_sha256": "b" * 64,
        "preparing_duration_ms": 10,
        "confirmed_success_duration_ms": 20,
        "cancellation_duration_ms": 5,
        "limit_failure_duration_ms": 5,
        "retry_duration_ms": 20,
        "total_duration_ms": 65,
        "peak_complete_process_tree_rss_bytes": 100_000,
        "complete_process_tree_rss_sample_count": 2,
        "peak_worker_process_commit_bytes": 50_000,
        "peak_worker_job_commit_bytes": 75_000,
        "sampled_worker_private_temp_peak_bytes": 25_000,
        "worker_private_temp_sample_count": 3,
        "worker_private_temp_measurement_complete": True,
        "peak_active_full_resolution_page_buffers": 1,
        "peak_active_page_results": 1,
        "peak_heavy_operation_concurrency": 1,
        "cancellation_acknowledgement_ms": 25,
        "limit_failure_code": "PDF_EXPORT_LIMIT_EXCEEDED",
        "limit_failure_operation_correlated": True,
        "worker_tree_terminated_after_cancel": True,
        "worker_tree_terminated_after_limit": True,
        "cancel_cleanup_complete": True,
        "limit_cleanup_complete": True,
        "retry_succeeded": True,
        "originals_unchanged": True,
        "manifest_expected_mutations_only": True,
        "manifest_unchanged_after_cancel_and_limit": True,
        "destination_absent_after_cancel": True,
        "destination_absent_after_limit": True,
        "overall_pass": True,
    }
    values.update(changes)
    return Pws0208ResourceEvidence(**values)  # type: ignore[arg-type]


def test_resource_evidence_round_trips_canonical_content_free_report() -> None:
    evidence = _evidence()
    encoded = encode_pws0208_resource_evidence(evidence)
    assert decode_pws0208_resource_evidence(encoded) == evidence
    assert evidence.output_sha256 not in repr(evidence)
    assert encoded.endswith(b"\n")
    assert b"path" not in encoded
    assert b"filename" not in encoded
    assert b"document_text" not in encoded
    assert b"machine" not in encoded


def test_zero_millisecond_cancellation_acknowledgement_is_valid() -> None:
    assert _evidence(cancellation_acknowledgement_ms=0).overall_pass is True


@pytest.mark.parametrize(
    "changes",
    (
        {"peak_complete_process_tree_rss_bytes": 2_000_000_000},
        {"peak_worker_process_commit_bytes": 600_000_000},
        {"peak_worker_job_commit_bytes": 900_000_000},
        {"sampled_worker_private_temp_peak_bytes": 600_000_000},
        {"peak_active_full_resolution_page_buffers": 2},
        {"peak_active_page_results": 2},
        {"peak_heavy_operation_concurrency": 2},
        {"cancellation_acknowledgement_ms": (PWS0208_MAXIMUM_CANCELLATION_ACKNOWLEDGEMENT_MS + 1)},
        {"worker_private_temp_measurement_complete": False},
        {"retry_succeeded": False},
        {"manifest_expected_mutations_only": False},
        {"manifest_unchanged_after_cancel_and_limit": False},
        {"limit_failure_code": "PDF_EXPORT_WORKER_FAILED"},
        {"limit_failure_operation_correlated": False},
        {"worker_tree_terminated_after_cancel": False},
        {"worker_tree_terminated_after_limit": False},
        {"total_duration_ms": 59},
        {"output_sha256": "B" * 64},
    ),
)
def test_resource_evidence_rejects_false_pass(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _evidence(**changes)


def test_resource_evidence_rejects_extensions_duplicates_and_noncanonical_bytes() -> None:
    encoded = encode_pws0208_resource_evidence(_evidence())
    document = json.loads(encoded)
    document["private_path"] = "PRIVATE-CONTENT-CANARY"
    with pytest.raises(ValueError):
        decode_pws0208_resource_evidence(json.dumps(document).encode())

    duplicate = encoded[:-2] + b',"report_version":1}\n'
    with pytest.raises(ValueError):
        decode_pws0208_resource_evidence(duplicate)

    with pytest.raises(ValueError):
        decode_pws0208_resource_evidence(b" " + encoded)


@pytest.mark.parametrize(
    "changes",
    (
        {"report_version": True},
        {"workload_version": 1.0},
        {"policy_version": True},
        {"source_count": 3.0},
        {"page_count": 12.0},
        {"maximum_active_full_resolution_page_buffers": True},
        {"maximum_active_page_results": 1.0},
        {"maximum_heavy_operation_concurrency": True},
        {"worker_private_temp_sampling_interval_ms": 50.0},
    ),
)
def test_resource_evidence_rejects_coerced_fixed_integer_fields(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        _evidence(**changes)


def test_committed_incremental_workload_has_exact_validated_identity() -> None:
    validate_workload(
        Path(__file__).parents[2] / "benchmarks" / "pws0208-searchable-mixed-workload-v1.json"
    )


def test_incremental_workload_rejects_changed_bytes(tmp_path: Path) -> None:
    changed = tmp_path / "changed.json"
    changed.write_bytes(
        (Path(__file__).parents[2] / "benchmarks" / "pws0208-searchable-mixed-workload-v1.json")
        .read_bytes()
        .replace(b'"page_count": 12', b'"page_count": 11')
    )
    with pytest.raises(ValueError):
        validate_workload(changed)


def test_report_reader_rejects_oversized_input_before_decode(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"x" * (16 * 1024 + 1))
    with pytest.raises(ValueError, match="size"):
        validate_report(oversized)

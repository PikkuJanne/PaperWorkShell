from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tools.m1_vertical_slice_benchmark import (
    LIVE_REPORT_ALREADY_EXISTS_CODE,
    LIVE_REPORT_FILENAME,
    LIVE_REPORT_OUTPUT_INVALID_CODE,
    LIVE_REPORT_PUBLISH_FAILED_CODE,
    M1BenchmarkError,
    M1BenchmarkReport,
    WorkerOperation,
    WorkerResourceMeasurement,
    WorkspaceCheckpoint,
    WorkspaceUsageMeasurement,
    _LiveM1ReportWriter,
    _open_live_m1_report_writer,
    build_m1_benchmark_report,
    decode_m1_benchmark_report,
    encode_m1_benchmark_report,
)
from tools.reference_profile import BenchmarkError, ReferenceAttestations, validate_profile

OBSERVATIONS: dict[str, object] = {
    "os_family": "Windows",
    "architecture": "x86_64",
    "logical_processors": 8,
    "memory_bytes": 16 * 1024 * 1024 * 1024,
    "storage_capacity_bytes": 980_000_000_000,
    "storage_free_bytes": 800_000_000_000,
    "graphics": None,
    "storage_media": None,
}


def _profile() -> dict[str, Any]:
    profile, issues = validate_profile()
    assert not issues
    assert profile is not None
    return profile


def _report(tmp_path: Path) -> M1BenchmarkReport:
    with patch(
        "tools.m1_vertical_slice_benchmark.observe_reference_environment",
        return_value=OBSERVATIONS,
    ):
        return build_m1_benchmark_report(
            _profile(),
            "limited-storage",
            tmp_path,
            attestations=ReferenceAttestations(),
        )


def _canonical_payload(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


@pytest.mark.parametrize(
    ("section", "field"),
    (
        ("root", "source_path"),
        ("vertical", "original_filename"),
        ("reference", "hostname"),
        ("observations", "document_text"),
    ),
)
def test_closed_report_rejects_path_identity_and_content_fields(
    tmp_path: Path,
    section: str,
    field: str,
) -> None:
    private_token = "PRIVATE-BENCHMARK-CONTENT-CANARY"
    payload = json.loads(encode_m1_benchmark_report(_report(tmp_path)))
    if section == "root":
        payload[field] = private_token
    elif section == "vertical":
        payload["vertical_slice"][field] = private_token
    elif section == "reference":
        payload["reference_environment"][field] = private_token
    else:
        payload["reference_environment"]["observations"][field] = private_token

    decoded, issues = decode_m1_benchmark_report(_canonical_payload(payload))
    rendered = "\n".join(issue.render() for issue in issues)

    assert decoded is None
    assert any(issue.code == "M1_REPORT_SCHEMA_INVALID" for issue in issues)
    assert private_token not in rendered


def test_schema_issues_do_not_echo_hostile_member_names(tmp_path: Path) -> None:
    private_token = "PRIVATE-MEMBER-NAME-CANARY"
    payload = json.loads(encode_m1_benchmark_report(_report(tmp_path)))
    payload[private_token] = True

    decoded, issues = decode_m1_benchmark_report(_canonical_payload(payload))
    rendered = "\n".join(issue.render() for issue in issues)

    assert decoded is None
    assert any(issue.code == "M1_REPORT_SCHEMA_INVALID" for issue in issues)
    assert private_token not in rendered


def test_extra_snapshot_attributes_are_never_serialized(tmp_path: Path) -> None:
    @dataclass(frozen=True)
    class SnapshotWithPrivateExtras:
        process_memory_limit_bytes: int = 100
        job_memory_limit_bytes: int = 200
        peak_process_memory_used_bytes: int = 25
        peak_job_memory_used_bytes: int = 50
        source_path: str = "C:/PRIVATE-CASE/source.pdf"
        original_filename: str = "PRIVATE-ORIGINAL.pdf"
        document_text: str = "PRIVATE-DOCUMENT-TEXT"

    @dataclass(frozen=True)
    class UsageWithPrivateExtras:
        case_bytes: int = 100
        cache_bytes: int = 20
        temp_bytes: int = 10
        entry_count: int = 3
        maximum_depth_seen: int = 2
        workspace_path: str = "C:/PRIVATE-CASE"

    worker = WorkerResourceMeasurement.from_snapshot(
        WorkerOperation.VERIFIED_EXPORT,
        SnapshotWithPrivateExtras(),
    )
    workspace = WorkspaceUsageMeasurement.from_usage(
        WorkspaceCheckpoint.EXPORTED,
        UsageWithPrivateExtras(),
    )
    rendered = repr((worker, workspace))

    assert "PRIVATE" not in rendered
    assert "path" not in rendered.casefold()


def test_result_code_cannot_carry_exception_or_path_text(tmp_path: Path) -> None:
    private_token = "C:/PRIVATE-CASE/source.pdf"
    payload = json.loads(encode_m1_benchmark_report(_report(tmp_path)))
    payload["vertical_slice"] = {
        "run_status": "failed",
        "result_code": f"M1_{private_token}",
        "phase_measurements": [{"phase": "case-create", "outcome": "failed", "elapsed_ns": 1}],
        "worker_resource_measurements": [],
        "workspace_usage_measurements": [],
    }

    decoded, issues = decode_m1_benchmark_report(_canonical_payload(payload))
    rendered = "\n".join(issue.render() for issue in issues)

    assert decoded is None
    assert any(issue.code == "M1_REPORT_SCHEMA_INVALID" for issue in issues)
    assert private_token not in rendered


def test_reference_observation_failure_does_not_echo_exception_text(tmp_path: Path) -> None:
    private_token = "C:/PRIVATE-CASE/source.pdf"
    with (
        patch(
            "tools.m1_vertical_slice_benchmark.observe_reference_environment",
            side_effect=BenchmarkError("BENCHMARK_IO_FAILED", private_token),
        ),
        pytest.raises(M1BenchmarkError) as raised,
    ):
        build_m1_benchmark_report(
            _profile(),
            "limited-storage",
            tmp_path,
            attestations=ReferenceAttestations(),
        )

    assert raised.value.code == "M1_OBSERVATION_FAILED"
    assert private_token not in raised.value.safe_message
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_work_directory_failure_clears_private_exception_chain(tmp_path: Path) -> None:
    private_token = "PRIVATE-WORK-DIRECTORY-CANARY"
    unavailable = tmp_path / private_token
    unavailable.write_text("not a directory", encoding="utf-8")

    with pytest.raises(M1BenchmarkError) as raised:
        build_m1_benchmark_report(
            _profile(),
            "limited-storage",
            unavailable,
            attestations=ReferenceAttestations(),
        )

    assert raised.value.code == "M1_OBSERVATION_FAILED"
    assert private_token not in raised.value.safe_message
    assert private_token not in repr(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_encoding_failure_clears_private_exception_chain(tmp_path: Path) -> None:
    private_token = "PRIVATE-ENCODER-CANARY"
    report = _report(tmp_path)

    with (
        patch.object(
            M1BenchmarkReport,
            "_to_payload",
            side_effect=ValueError(private_token),
        ),
        pytest.raises(M1BenchmarkError) as raised,
    ):
        encode_m1_benchmark_report(report)

    assert raised.value.code == "M1_REPORT_INVALID"
    assert private_token not in raised.value.safe_message
    assert private_token not in repr(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_host_only_report_contains_no_gate_pass_or_product_threshold(tmp_path: Path) -> None:
    encoded = encode_m1_benchmark_report(_report(tmp_path))
    payload = json.loads(encoded)

    assert payload["reference_environment"]["evaluation"]["status"] == "mismatched"
    assert payload["vertical_slice"]["run_status"] == "not-run"
    assert payload["measurement_policy"] == {"product_targets": [], "status": "record-only"}
    assert "gate_pass" not in encoded.decode()
    assert "qualified" not in encoded.decode()
    assert "C:/" not in encoded.decode()


def test_live_writer_rejects_reparse_output_directory_without_disclosure(
    tmp_path: Path,
) -> None:
    private_token = "PRIVATE-REPARSE-OUTPUT-CANARY"
    target = tmp_path / private_token
    target.mkdir()
    redirected = tmp_path / "redirected-output"
    try:
        redirected.symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory reparse creation is unavailable ({error.winerror})")

    with pytest.raises(M1BenchmarkError) as raised:
        _open_live_m1_report_writer(redirected)

    assert raised.value.code == LIVE_REPORT_OUTPUT_INVALID_CODE
    assert private_token not in raised.value.safe_message
    assert private_token not in repr(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert list(target.iterdir()) == []


def test_live_writer_output_authentication_sanitizes_unexpected_failure(
    tmp_path: Path,
) -> None:
    private_token = "PRIVATE-OUTPUT-AUTH-CANARY"
    with (
        patch(
            "tools.m1_vertical_slice_benchmark.open_handle",
            side_effect=OSError(private_token),
        ),
        pytest.raises(M1BenchmarkError) as raised,
    ):
        _open_live_m1_report_writer(tmp_path)

    assert raised.value.code == LIVE_REPORT_OUTPUT_INVALID_CODE
    assert private_token not in raised.value.safe_message
    assert private_token not in repr(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_live_writer_no_replace_preserves_preexisting_hardlink_target(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    outside = tmp_path / "outside-report"
    original = b"PREEXISTING-PRIVATE-REPORT"
    outside.write_bytes(original)
    destination = output / LIVE_REPORT_FILENAME
    try:
        destination.hardlink_to(outside)
    except OSError as error:
        pytest.skip(f"hardlink creation is unavailable ({error.winerror})")

    with (
        _open_live_m1_report_writer(output) as writer,
        patch.object(_LiveM1ReportWriter, "_encode_completed_report", return_value=b"{}\n"),
        patch(
            "tools.m1_vertical_slice_benchmark._validate_authenticated_live_report",
        ),
        pytest.raises(M1BenchmarkError) as raised,
    ):
        writer._publish_completed_report(_report(tmp_path / "host-observation"))

    assert raised.value.code == LIVE_REPORT_ALREADY_EXISTS_CODE
    assert outside.read_bytes() == original
    assert destination.read_bytes() == original
    assert not any(path.name.startswith(".pws-0114-m1-report-") for path in output.iterdir())


def test_live_writer_publication_failure_is_sanitized_and_removes_stage(
    tmp_path: Path,
) -> None:
    private_token = "PRIVATE-PUBLISH-FAILURE-CANARY"
    with (
        _open_live_m1_report_writer(tmp_path) as writer,
        patch.object(_LiveM1ReportWriter, "_encode_completed_report", return_value=b"{}\n"),
        patch(
            "tools.m1_vertical_slice_benchmark._validate_authenticated_live_report",
        ),
        patch(
            "tools.m1_vertical_slice_benchmark.rename_open_file_no_replace_at",
            side_effect=OSError(private_token),
        ),
        pytest.raises(M1BenchmarkError) as raised,
    ):
        writer._publish_completed_report(_report(tmp_path / "host-observation"))

    assert raised.value.code == LIVE_REPORT_PUBLISH_FAILED_CODE
    assert private_token not in raised.value.safe_message
    assert private_token not in repr(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert not any(path.name.startswith(".pws-0114-m1-report-") for path in tmp_path.iterdir())


def test_live_writer_internal_encoding_failure_clears_private_exception_chain(
    tmp_path: Path,
) -> None:
    private_token = "PRIVATE-LIVE-ENCODER-CANARY"
    with (
        _open_live_m1_report_writer(tmp_path) as writer,
        patch(
            "tools.m1_vertical_slice_benchmark._encode_live_m1_completed_report",
            side_effect=ValueError(private_token),
        ),
        pytest.raises(M1BenchmarkError) as raised,
    ):
        writer._encode_completed_report(_report(tmp_path / "host-observation"))

    assert raised.value.code == "M1_REPORT_INVALID"
    assert private_token not in raised.value.safe_message
    assert private_token not in repr(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None

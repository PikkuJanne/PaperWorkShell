from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast
from unittest.mock import patch
from uuid import UUID

import pytest

from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from tools.m1_vertical_slice_benchmark import (
    _LIVE_M1_REPORT_AUTHORITY_MARKER,
    LIVE_REPORT_PUBLISH_FAILED_CODE,
    MAX_REPORT_BYTES,
    REPORT_SCHEMA_PATH,
    FailedRunEvidence,
    FailureStage,
    M1BenchmarkError,
    M1BenchmarkReport,
    _build_live_m1_failed_report,
    _encode_live_m1_failed_report,
    _LiveM1ReportAuthority,
    _open_live_m1_report_writer,
    build_m1_benchmark_report,
    decode_m1_benchmark_report,
    encode_m1_benchmark_report,
)
from tools.m1_vertical_slice_live_runner import main as live_runner_main

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _failed_report_payload() -> bytes:
    authority = _LiveM1ReportAuthority(_LIVE_M1_REPORT_AUTHORITY_MARKER)
    evidence = FailedRunEvidence(
        run_id=UUID("abcdefab-1234-4234-9234-abcdefabcdef"),
        failure_stage=FailureStage.SETUP,
        total_elapsed_ns=1,
        repository_revision="a" * 40,
        repository_tree="b" * 40,
        repository_worktree_clean=True,
        report_schema_sha256=hashlib.sha256(
            (PROJECT_ROOT / REPORT_SCHEMA_PATH).read_bytes()
        ).hexdigest(),
        budget_results=(),
    )
    report = _build_live_m1_failed_report(
        authority,
        build_m1_benchmark_report(CONSTRAINED_RESOURCE_POLICY_V1),
        result_code="M1_LIVE_CHILD_FAILED",
        phase_measurements=(),
        worker_resource_measurements=(),
        workspace_usage_measurements=(),
        failed_evidence=evidence,
    )
    return _encode_live_m1_failed_report(authority, report)


def _payload() -> dict[str, object]:
    return cast(
        dict[str, object],
        json.loads(
            encode_m1_benchmark_report(build_m1_benchmark_report(CONSTRAINED_RESOURCE_POLICY_V1))
        ),
    )


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def test_resource_report_surface_is_content_free_and_path_free() -> None:
    payload = _payload()
    rendered = json.dumps(payload, sort_keys=True).lower()
    prohibited = (
        "filename",
        "full_path",
        "source_path",
        "ocr_text",
        "form_value",
        "metadata_value",
        "page_image",
        "signature_image",
        "user_name",
        "address",
        "reference_environment",
        "attestation",
    )
    assert all(token not in rendered for token in prohibited)


def test_failed_report_surface_is_content_free_and_path_free() -> None:
    rendered = _failed_report_payload().decode("utf-8").casefold()
    for prohibited in (
        "filename",
        "full_path",
        "source_path",
        "ocr_text",
        "form_value",
        "metadata_value",
        "page_image",
        "signature_image",
        "user_name",
        "address",
    ):
        assert prohibited not in rendered


def test_failed_result_code_cannot_carry_exception_or_path_text() -> None:
    value = json.loads(_failed_report_payload())
    private_token = "C:/PRIVATE-CASE/source.pdf"
    value["vertical_slice"]["result_code"] = f"M1_{private_token}"
    decoded, issues = decode_m1_benchmark_report(_canonical(value))
    assert decoded is None
    assert issues[0].code == "M1_REPORT_SCHEMA_INVALID"
    assert private_token not in issues[0].render()


def test_failed_report_publication_sanitizes_private_storage_error(tmp_path: Path) -> None:
    report, issues = decode_m1_benchmark_report(_failed_report_payload())
    assert issues == []
    assert type(report) is M1BenchmarkReport
    private_token = "C:/PRIVATE-CASE/report.json"
    with (
        _open_live_m1_report_writer(tmp_path) as writer,
        patch(
            "tools.m1_vertical_slice_benchmark.rename_open_file_no_replace_at",
            side_effect=OSError(private_token),
        ),
        pytest.raises(M1BenchmarkError) as raised,
    ):
        writer._publish_failed_report(report)

    assert raised.value.code == LIVE_REPORT_PUBLISH_FAILED_CODE
    assert private_token not in raised.value.safe_message
    assert private_token not in repr(raised.value)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("section", "field"),
    (
        ("root", "source_filename"),
        ("limits", "document_path"),
        ("vertical", "ocr_text"),
    ),
)
def test_closed_schema_rejects_prohibited_report_fields(section: str, field: str) -> None:
    payload = _payload()
    if section == "root":
        payload[field] = "PRIVATE-CONTENT"
    elif section == "limits":
        payload["resource_limits"][field] = "PRIVATE-CONTENT"  # type: ignore[index]
    else:
        payload["vertical_slice"][field] = "PRIVATE-CONTENT"  # type: ignore[index]
    decoded, issues = decode_m1_benchmark_report(_canonical(payload))
    assert decoded is None
    assert issues[0].code == "M1_REPORT_SCHEMA_INVALID"
    assert "PRIVATE-CONTENT" not in issues[0].render()


def test_invalid_policy_error_does_not_echo_private_object_representation() -> None:
    private_token = "PRIVATE-POLICY-PATH-CANARY"

    class _PrivatePolicy:
        def __repr__(self) -> str:
            return private_token

    with pytest.raises(M1BenchmarkError) as raised:
        build_m1_benchmark_report(_PrivatePolicy())  # type: ignore[arg-type]
    assert raised.value.code == "M1_RESOURCE_POLICY_INVALID"
    assert private_token not in raised.value.safe_message


def test_bounded_decoder_rejects_oversized_input_without_parsing_content() -> None:
    private_token = b"PRIVATE-OVERSIZED-REPORT-CONTENT"
    decoded, issues = decode_m1_benchmark_report(private_token + b"x" * (MAX_REPORT_BYTES + 1))
    assert decoded is None
    assert issues[0].code == "M1_REPORT_SIZE_INVALID"
    assert private_token.decode() not in issues[0].render()


def test_deep_json_fails_safely_without_echoing_content() -> None:
    private_token = "PRIVATE-DEEP-JSON-CONTENT"
    payload = ("[" * 1_000 + json.dumps(private_token) + "]" * 1_000).encode()
    decoded, issues = decode_m1_benchmark_report(payload)
    assert decoded is None
    assert issues[0].code in {"M1_REPORT_JSON_INVALID", "M1_REPORT_ROOT_INVALID"}
    assert private_token not in issues[0].render()


def test_runner_help_has_no_operator_hardware_attestation_surface(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        live_runner_main(["--help"])
    assert raised.value.code == 0
    help_text = capsys.readouterr().out
    for obsolete in (
        "--attest-memory-mib",
        "--attest-graphics",
        "--attest-storage-media",
        "hdd-or-validated-emulation",
    ):
        assert obsolete not in help_text
    assert "--profile {constrained}" in help_text


def test_policy_report_does_not_inspect_host_hardware(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("host probing is forbidden")

    monkeypatch.setattr("os.cpu_count", forbidden)
    encoded = encode_m1_benchmark_report(build_m1_benchmark_report(CONSTRAINED_RESOURCE_POLICY_V1))
    assert b'"processing_profile":"constrained"' in encoded

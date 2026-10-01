"""Focused tests for the bounded flattened-PDF worker report codec."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from paperworkshell.domain.pdf_optimization import (
    PdfOptimizationOutcome,
    PdfOptimizationProfile,
    PdfOptimizationReport,
)
from paperworkshell.overlays.exporting import (
    FlattenedPdfExportError,
    FlattenedPdfExportErrorCode,
    FlattenedPdfExportVerificationReport,
)
from paperworkshell.worker.flattened_pdf_export_report import (
    FlattenedPdfExportWorkerReport,
    decode_flattened_pdf_worker_report,
    encode_flattened_pdf_worker_report,
)
from tests.unit.worker.flattened_pdf_export_fixtures import (
    synthetic_flattened_plan,
    uuid4,
)


def _worker_report() -> FlattenedPdfExportWorkerReport:
    plan = synthetic_flattened_plan()
    return FlattenedPdfExportWorkerReport(
        operation_id=uuid4(20),
        output_object_id=uuid4(21),
        report=FlattenedPdfExportVerificationReport.for_verified_plan(
            plan,
            output_sha256="5" * 64,
            output_byte_size=2_048,
        ),
    )


def test_flattened_worker_report_round_trips_and_binds_base_evidence() -> None:
    value = _worker_report()

    payload = encode_flattened_pdf_worker_report(value)
    decoded = decode_flattened_pdf_worker_report(payload, plan=value.report.plan)

    assert decoded == value
    assert decoded.report.base_report.output_sha256 == "5" * 64
    assert decoded.report.checks == value.report.checks
    assert payload == encode_flattened_pdf_worker_report(decoded)


def test_flattened_wrapper_retains_v2_base_optimization_without_wrapper_version_change() -> None:
    legacy = _worker_report()
    optimized = replace(
        legacy.report.base_report,
        report_version=2,
        optimization=PdfOptimizationReport(
            PdfOptimizationProfile.SAFE_OBJECT_STREAM_V1,
            PdfOptimizationOutcome.OPTIMIZED,
            4_096,
            2_048,
            2_048,
            legacy.report.base_report.page_count,
        ),
    )
    value = replace(legacy, report=replace(legacy.report, base_report=optimized))

    payload = encode_flattened_pdf_worker_report(value)
    decoded = decode_flattened_pdf_worker_report(payload, plan=value.report.plan)

    assert decoded == value
    assert decoded.worker_report_version == legacy.worker_report_version
    assert decoded.report.report_version == legacy.report.report_version
    assert decoded.report.base_report.optimization == optimized.optimization


@pytest.mark.parametrize(
    ("member", "value"),
    (
        ("worker_report_version", 2),
        ("report_version", 2),
        ("semantic_fingerprint", "0" * 64),
        ("operation_id", "00000000-0000-4000-8000-000000000021"),
    ),
)
def test_flattened_worker_report_rejects_tampered_bindings(
    member: str,
    value: object,
) -> None:
    report = _worker_report()
    document = json.loads(encode_flattened_pdf_worker_report(report))
    document[member] = value
    tampered = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("ascii")

    with pytest.raises(FlattenedPdfExportError) as captured:
        decode_flattened_pdf_worker_report(tampered, plan=report.report.plan)

    assert captured.value.code is FlattenedPdfExportErrorCode.REPORT_INVALID


def test_flattened_worker_report_rejects_noncanonical_json_and_wrong_plan() -> None:
    report = _worker_report()
    payload = encode_flattened_pdf_worker_report(report)
    other_plan = synthetic_flattened_plan()
    object.__setattr__(other_plan, "semantic_fingerprint", "0" * 64)

    with pytest.raises(FlattenedPdfExportError):
        decode_flattened_pdf_worker_report(b" " + payload, plan=report.report.plan)
    with pytest.raises(FlattenedPdfExportError):
        decode_flattened_pdf_worker_report(payload, plan=other_plan)

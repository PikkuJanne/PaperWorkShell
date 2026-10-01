"""Tests for the bounded correlated PDF-export worker report."""

from __future__ import annotations

import json
from dataclasses import replace
from uuid import UUID

import pytest

from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    PdfExportPage,
    PdfExportPlan,
    PdfExportRotation,
    PdfExportSource,
    PdfExportSourceSignatureState,
    PdfExportVerificationReport,
)
from paperworkshell.domain.pdf_optimization import (
    PdfOptimizationOutcome,
    PdfOptimizationProfile,
    PdfOptimizationReport,
)
from paperworkshell.worker.pdf_export_report import (
    DEFAULT_PDF_EXPORT_ENGINE_PROFILE,
    MAX_PDF_EXPORT_WORKER_REPORT_BYTES,
    PdfExportWorkerReport,
    decode_pdf_export_worker_report,
    encode_pdf_export_worker_report,
)


def _uuid(value: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{value:012x}")


def _plan() -> PdfExportPlan:
    source = PdfExportSource(
        source_id=_uuid(10),
        byte_size=100,
        page_count=1,
        sha256="a" * 64,
        signature_state=PdfExportSourceSignatureState.MARKERS_DETECTED,
    )
    return PdfExportPlan(
        case_id=_uuid(1),
        manifest_revision=2,
        manifest_sha256="b" * 64,
        manifest_cas_token="synthetic_export_cas_001",
        sources=(source,),
        pages=(PdfExportPage(0, _uuid(20), _uuid(10), 0, PdfExportRotation.DEGREES_90),),
    )


def _worker_report() -> PdfExportWorkerReport:
    report = PdfExportVerificationReport.for_verified_plan(
        _plan(),
        output_sha256="c" * 64,
        output_byte_size=1_024,
    )
    return PdfExportWorkerReport(_uuid(30), _uuid(31), report)


def _mutate(*, root_updates: dict[str, object] | None = None) -> bytes:
    document = json.loads(encode_pdf_export_worker_report(_worker_report()))
    if root_updates is not None:
        document.update(root_updates)
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("ascii")


def test_worker_report_round_trip_is_canonical_typed_and_redacted() -> None:
    report = _worker_report()

    first = encode_pdf_export_worker_report(report)
    second = encode_pdf_export_worker_report(report)
    decoded = decode_pdf_export_worker_report(first)

    assert first == second
    assert len(first) < MAX_PDF_EXPORT_WORKER_REPORT_BYTES
    assert decoded == report
    assert decoded.engine_profile == DEFAULT_PDF_EXPORT_ENGINE_PROFILE
    rendered = repr(decoded)
    assert "synthetic_export_cas_001" not in rendered
    assert "a" * 64 not in rendered
    assert "b" * 64 not in rendered
    assert "c" * 64 not in rendered


@pytest.mark.parametrize(
    "payload",
    (
        b"",
        b"not-json",
        b"{} trailing",
        b"\xff",
        b'{"worker_report_version":1,"worker_report_version":1}',
        b"[1.5]",
        b"[NaN]",
        b"[9007199254740992]",
    ),
    ids=("empty", "json", "surplus", "utf8", "duplicate", "float", "nan", "integer"),
)
def test_worker_report_rejects_malformed_or_unbounded_json(payload: bytes) -> None:
    with pytest.raises(PdfExportError) as raised:
        decode_pdf_export_worker_report(payload)

    assert raised.value.code is PdfExportErrorCode.REPORT_INVALID


@pytest.mark.parametrize(
    "updates",
    (
        {"worker_report_version": 2},
        {"operation_id": str(_uuid(31))},
        {"engine_profile": {}},
        {"report": {}},
        {"unexpected": 1},
    ),
)
def test_worker_report_rejects_version_identity_and_shape_tampering(
    updates: dict[str, object],
) -> None:
    with pytest.raises(PdfExportError) as raised:
        decode_pdf_export_worker_report(_mutate(root_updates=updates))

    assert raised.value.code is PdfExportErrorCode.REPORT_INVALID


def test_worker_report_rejects_digest_checks_and_engine_tampering() -> None:
    document = json.loads(encode_pdf_export_worker_report(_worker_report()))

    for mutation in ("digest", "checks", "engine"):
        changed = json.loads(json.dumps(document))
        if mutation == "digest":
            changed["report"]["output_sha256"] = "0" * 64
            changed["report"]["semantic_fingerprint"] = "0" * 64
        elif mutation == "checks":
            changed["report"]["checks"] = changed["report"]["checks"][:-1]
        else:
            changed["engine_profile"]["pikepdf"] = "0.0.0"
        payload = json.dumps(changed, sort_keys=True, separators=(",", ":")).encode("ascii")

        if mutation == "digest":
            # Digests are correlated against the plan/RPC output by the broker;
            # the bounded report codec only validates their exact representation.
            decoded = decode_pdf_export_worker_report(payload)
            assert decoded.report.output_sha256 == "0" * 64
            continue
        with pytest.raises(PdfExportError) as raised:
            decode_pdf_export_worker_report(payload)
        assert raised.value.code is PdfExportErrorCode.REPORT_INVALID


def test_worker_report_size_is_bounded_before_json_parsing() -> None:
    with pytest.raises(PdfExportError) as raised:
        decode_pdf_export_worker_report(b"x" * (MAX_PDF_EXPORT_WORKER_REPORT_BYTES + 1))

    assert raised.value.code is PdfExportErrorCode.REPORT_INVALID


def _optimized_worker_report(
    outcome: PdfOptimizationOutcome = PdfOptimizationOutcome.OPTIMIZED,
) -> PdfExportWorkerReport:
    legacy = _worker_report()
    optimized = outcome is PdfOptimizationOutcome.OPTIMIZED
    optimization = PdfOptimizationReport(
        profile=PdfOptimizationProfile.SAFE_OBJECT_STREAM_V1,
        outcome=outcome,
        baseline_byte_size=2_048 if optimized else 1_024,
        candidate_byte_size=None if outcome is PdfOptimizationOutcome.SKIPPED_BUDGET else 1_024,
        selected_byte_size=1_024,
        verified_page_count=1 if optimized else 0,
    )
    return replace(
        legacy,
        report=replace(legacy.report, report_version=2, optimization=optimization),
    )


@pytest.mark.parametrize("outcome", tuple(PdfOptimizationOutcome))
def test_v2_optimization_outcomes_round_trip_inside_unchanged_pipe_bound(
    outcome: PdfOptimizationOutcome,
) -> None:
    value = _optimized_worker_report(outcome)

    payload = encode_pdf_export_worker_report(value)

    assert len(payload) < MAX_PDF_EXPORT_WORKER_REPORT_BYTES
    assert decode_pdf_export_worker_report(payload) == value
    assert encode_pdf_export_worker_report(decode_pdf_export_worker_report(payload)) == payload
    document = json.loads(payload)
    assert document["worker_report_version"] == 1
    assert document["report"]["report_version"] == 2
    assert document["report"]["optimization"]["report_version"] == 1


def test_legacy_report_wire_shape_is_unchanged_and_rejects_new_fields() -> None:
    payload = encode_pdf_export_worker_report(_worker_report())
    document = json.loads(payload)
    assert document["report"]["report_version"] == 1
    assert set(document["report"]) == {
        "report_version",
        "semantic_fingerprint",
        "output_sha256",
        "output_byte_size",
        "page_count",
        "checks",
        "unsigned_derivative",
    }
    assert decode_pdf_export_worker_report(payload).report.optimization is None
    document["report"]["optimization"] = None
    with pytest.raises(PdfExportError) as captured:
        decode_pdf_export_worker_report(json.dumps(document).encode("ascii"))
    assert captured.value.code is PdfExportErrorCode.REPORT_INVALID


@pytest.mark.parametrize(
    ("member", "value"),
    (
        ("report_version", 3),
        ("report_version", True),
        ("optimization", None),
        ("optimization", {}),
        ("output_byte_size", 1_025),
        ("unexpected", "PRIVATE_REPORT_CANARY"),
    ),
)
def test_v2_report_rejects_version_shape_and_selected_size_mismatch(
    member: str, value: object
) -> None:
    document = json.loads(encode_pdf_export_worker_report(_optimized_worker_report()))
    document["report"][member] = value

    with pytest.raises(PdfExportError) as captured:
        decode_pdf_export_worker_report(json.dumps(document).encode("ascii"))

    assert captured.value.code is PdfExportErrorCode.REPORT_INVALID
    assert "PRIVATE_REPORT_CANARY" not in str(captured.value)


@pytest.mark.parametrize(
    ("member", "value"),
    (
        ("report_version", 2),
        ("profile", "PRIVATE_PROFILE_CANARY"),
        ("outcome", "PRIVATE_OUTCOME_CANARY"),
        ("baseline_byte_size", 1_024),
        ("candidate_byte_size", None),
        ("candidate_byte_size", 1_025),
        ("candidate_byte_size", True),
        ("selected_byte_size", 1_025),
        ("verified_page_count", 0),
        ("verified_page_count", 2),
        ("verified_page_count", True),
        ("unexpected", "PRIVATE_REPORT_CANARY"),
    ),
)
def test_v2_report_rejects_unverified_or_contradictory_optimization_without_raw_context(
    member: str, value: object
) -> None:
    document = json.loads(encode_pdf_export_worker_report(_optimized_worker_report()))
    document["report"]["optimization"][member] = value

    with pytest.raises(PdfExportError) as captured:
        decode_pdf_export_worker_report(json.dumps(document).encode("ascii"))

    assert captured.value.code is PdfExportErrorCode.REPORT_INVALID
    assert "PRIVATE_" not in str(captured.value)
    assert captured.value.__context__ is None
    assert captured.value.__cause__ is None


def test_v2_report_requires_optimization_and_rejects_duplicate_optimization_fields() -> None:
    document = json.loads(encode_pdf_export_worker_report(_optimized_worker_report()))
    del document["report"]["optimization"]
    with pytest.raises(PdfExportError):
        decode_pdf_export_worker_report(json.dumps(document).encode("ascii"))
    payload = encode_pdf_export_worker_report(_optimized_worker_report()).replace(
        b'"selected_byte_size":1024',
        b'"selected_byte_size":1024,"selected_byte_size":1024',
    )
    with pytest.raises(PdfExportError):
        decode_pdf_export_worker_report(payload)

"""Closed versioned evidence cannot acquire authority through JSON coercion."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from paperworkshell.domain.pdf_export import PdfExportError
from paperworkshell.domain.pdf_image_stages import PdfImageStageRequest
from paperworkshell.worker.pdf_image_stage_wire import (
    PdfImageStageWorkerResult,
    decode_pdf_image_stage_evidence,
    decode_pdf_image_stage_plan,
    decode_pdf_image_stage_result,
    encode_pdf_image_stage_evidence,
    encode_pdf_image_stage_plan,
    encode_pdf_image_stage_result,
)
from tests.unit.test_pdf_image_stages import _analysis, _report, _request
from tests.unit.worker.pdf_image_stage_fixtures import StageSystem
from tests.unit.worker.test_pdf_export_broker import _plan, _uuid


@pytest.mark.parametrize("intent", (PdfImageStageRequest(), _request()))
def test_plan_round_trip_preserves_explicit_intent_and_hides_authority(
    intent: PdfImageStageRequest,
) -> None:
    plan = _plan()
    handles = {source.source_id: index + 900 for index, source in enumerate(plan.sources)}
    payload = encode_pdf_image_stage_plan(plan, handles, intent)
    value = decode_pdf_image_stage_plan(payload)
    assert value.base.plan == plan and value.request == intent
    assert value.base.handle_for(plan.sources[0].source_id) == 900
    assert plan.manifest_sha256 not in repr(value)
    assert "900" not in repr(value)


def test_evidence_and_small_header_round_trip_separate_stage_and_baseline() -> None:
    analysis, report = _analysis(), _report()
    payload = encode_pdf_image_stage_evidence(analysis, report)
    assert decode_pdf_image_stage_evidence(payload) == (analysis, report)
    system = StageSystem()
    value = PdfImageStageWorkerResult(
        _uuid(60),
        _uuid(61),
        _uuid(62),
        system.baseline,
        system.report.output_byte_size,
        system.report.output_sha256,
        len(payload),
        "a" * 64,
    )
    assert decode_pdf_image_stage_result(encode_pdf_image_stage_result(value)) == value
    assert system.report.output_sha256 not in repr(value)


@pytest.mark.parametrize("field", ("evidence_version", "analysis", "report"))
def test_evidence_rejects_missing_members(field: str) -> None:
    raw = json.loads(encode_pdf_image_stage_evidence(_analysis(), _report()))
    del raw[field]
    with pytest.raises(PdfExportError):
        decode_pdf_image_stage_evidence(json.dumps(raw).encode())


@pytest.mark.parametrize(
    "mutation",
    ("future", "unknown", "bool", "duplicate", "noncanonical", "oversize", "deep", "invalid_enum"),
)
def test_hostile_evidence_fails_without_private_error_chain(mutation: str) -> None:
    raw = json.loads(encode_pdf_image_stage_evidence(_analysis(), _report()))
    if mutation == "future":
        raw["evidence_version"] = 2
    if mutation == "unknown":
        raw["PRIVATE_STAGE_CANARY"] = "PRIVATE_STAGE_CANARY"
    if mutation == "bool":
        raw["analysis"]["page_count"] = True
    if mutation == "invalid_enum":
        raw["report"]["profile"] = "PRIVATE_STAGE_CANARY"
    payload = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    if mutation == "duplicate":
        payload = payload.replace(
            b'"evidence_version":1', b'"evidence_version":1,"evidence_version":1'
        )
    if mutation == "noncanonical":
        payload += b" "
    if mutation == "oversize":
        payload = b" " * (2 * 1024 * 1024 + 1)
    if mutation == "deep":
        payload = b"[" * 100 + b"0" + b"]" * 100
    with pytest.raises(PdfExportError) as caught:
        decode_pdf_image_stage_evidence(payload)
    assert caught.value.__context__ is None
    assert "PRIVATE_STAGE_CANARY" not in str(caught.value)


def test_header_rejects_forged_correlated_baseline_identity() -> None:
    system = StageSystem()
    value = PdfImageStageWorkerResult(
        _uuid(60),
        _uuid(61),
        _uuid(62),
        system.baseline,
        system.report.output_byte_size,
        system.report.output_sha256,
        100,
        "a" * 64,
    )
    raw = json.loads(encode_pdf_image_stage_result(value))
    raw["baseline"]["operation_id"] = str(_uuid(90))
    with pytest.raises(PdfExportError):
        decode_pdf_image_stage_result(json.dumps(raw).encode())
    with pytest.raises(PdfExportError):
        replace(value, output_byte_size=system.baseline.output_byte_size + 1)


def test_evidence_node_limit_applies_independently_of_byte_limit() -> None:
    # A short array can exhaust the structural traversal cap well before 2 MiB.
    payload = b"[" + b"0," * 65_536 + b"0]"
    assert len(payload) < 2 * 1024 * 1024
    with pytest.raises(PdfExportError):
        decode_pdf_image_stage_evidence(payload)


@pytest.mark.parametrize(
    "field,value",
    (
        ("request_version", True),
        ("request_version", 2),
        ("selected_asset_ids", [True]),
        ("profile", "PRIVATE_STAGE_PROFILE"),
    ),
)
def test_plan_rejects_coercion_and_unknown_profile_without_retaining_values(
    field: str, value: object
) -> None:
    plan = _plan()
    raw = json.loads(
        encode_pdf_image_stage_plan(
            plan,
            {item.source_id: 900 + index for index, item in enumerate(plan.sources)},
            _request(),
        )
    )
    raw["request"][field] = value
    with pytest.raises(PdfExportError) as caught:
        decode_pdf_image_stage_plan(json.dumps(raw).encode())
    assert caught.value.__context__ is None
    assert "PRIVATE_STAGE_PROFILE" not in repr(caught.value)

"""Focused tests for the canonical flattened-PDF worker plan codec."""

from __future__ import annotations

import json

import pytest

from paperworkshell.overlays.exporting import (
    FlattenedPdfExportError,
    FlattenedPdfExportErrorCode,
)
from paperworkshell.worker.flattened_pdf_export_plan import (
    decode_flattened_pdf_worker_plan,
    encode_flattened_pdf_worker_plan,
)
from tests.unit.worker.flattened_pdf_export_fixtures import synthetic_flattened_plan


def test_flattened_worker_plan_round_trips_exact_handle_bindings() -> None:
    plan = synthetic_flattened_plan()
    source_id = plan.base_plan.sources[0].source_id
    asset_id = plan.assets[0].asset_id

    payload = encode_flattened_pdf_worker_plan(plan, {source_id: 101}, {asset_id: 103})
    decoded = decode_flattened_pdf_worker_plan(payload)

    assert decoded.plan == plan
    assert decoded.sources[0].worker_handle == 101
    assert decoded.assets[0].worker_handle == 103
    assert decoded.assets[0].asset.raster_asset.pixel_sha256 == "4" * 64
    assert payload == encode_flattened_pdf_worker_plan(
        decoded.plan,
        {source_id: 101},
        {asset_id: 103},
    )
    assert "101" not in repr(decoded)
    assert "103" not in repr(decoded)


@pytest.mark.parametrize(
    ("source_handles", "raster_handles"),
    (
        ({}, {}),
        ({}, None),
        ({"source": 101}, {"asset": 103}),
    ),
)
def test_flattened_worker_plan_rejects_inexact_handle_maps(
    source_handles: object,
    raster_handles: object,
) -> None:
    plan = synthetic_flattened_plan()

    with pytest.raises(FlattenedPdfExportError) as captured:
        encode_flattened_pdf_worker_plan(plan, source_handles, raster_handles)  # type: ignore[arg-type]

    assert captured.value.code is FlattenedPdfExportErrorCode.MODEL_INVALID


def test_flattened_worker_plan_rejects_handle_reuse_across_sources_and_rasters() -> None:
    plan = synthetic_flattened_plan()

    with pytest.raises(FlattenedPdfExportError) as captured:
        encode_flattened_pdf_worker_plan(
            plan,
            {plan.base_plan.sources[0].source_id: 101},
            {plan.assets[0].asset_id: 101},
        )

    assert captured.value.code is FlattenedPdfExportErrorCode.MODEL_INVALID


@pytest.mark.parametrize(
    ("member", "value"),
    (
        ("worker_plan_version", 2),
        ("plan_version", 2),
        ("semantic_fingerprint", "0" * 64),
    ),
)
def test_flattened_worker_plan_rejects_future_or_tampered_roots(
    member: str,
    value: object,
) -> None:
    plan = synthetic_flattened_plan()
    payload = encode_flattened_pdf_worker_plan(
        plan,
        {plan.base_plan.sources[0].source_id: 101},
        {plan.assets[0].asset_id: 103},
    )
    document = json.loads(payload)
    document[member] = value
    tampered = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("ascii")

    with pytest.raises(FlattenedPdfExportError) as captured:
        decode_flattened_pdf_worker_plan(tampered)

    assert captured.value.code is FlattenedPdfExportErrorCode.RESULT_INVALID


def test_flattened_worker_plan_rejects_noncanonical_and_duplicate_json() -> None:
    plan = synthetic_flattened_plan()
    payload = encode_flattened_pdf_worker_plan(
        plan,
        {plan.base_plan.sources[0].source_id: 101},
        {plan.assets[0].asset_id: 103},
    )

    with pytest.raises(FlattenedPdfExportError):
        decode_flattened_pdf_worker_plan(b" " + payload)
    with pytest.raises(FlattenedPdfExportError):
        decode_flattened_pdf_worker_plan(b'{"worker_plan_version":1,"worker_plan_version":1}')

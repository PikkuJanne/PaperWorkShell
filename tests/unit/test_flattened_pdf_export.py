"""Focused pure-contract evidence for PWS-0307 flattened PDF export."""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.pdf_export import (
    MAX_PDF_EXPORT_PAGES,
    PdfExportHistoryRetryReceipt,
    PdfExportOutcome,
    PdfExportOutcomeStatus,
    PdfExportRotation,
)
from paperworkshell.overlays.exporting import (
    FLATTENED_PDF_EXPORT_PLAN_VERSION,
    FlattenedPdfExportCheckCode,
    FlattenedPdfExportError,
    FlattenedPdfExportErrorCode,
    FlattenedPdfExportOutcome,
    FlattenedPdfExportPlan,
    FlattenedPdfExportVerificationReport,
    build_flattened_pdf_export_plan,
    build_flattened_pdf_export_plan_for_pages,
    required_visible_signature_assets,
    required_visible_signature_assets_for_pages,
)
from paperworkshell.overlays.models import (
    SIGNATURE_IMAGE_TERMINOLOGY,
    SignatureImageOverlayPayload,
)
from paperworkshell.overlays.rendering import OverlayRasterAsset

CASE_ID = UUID("10000000-0000-4000-8000-000000000701")
SOURCE_ID = UUID("20000000-0000-4000-8000-000000000701")
SECOND_SOURCE_ID = UUID("20000000-0000-4000-8000-000000000702")
ACTIVE_PAGE = UUID("30000000-0000-4000-8000-000000000701")
INACTIVE_PAGE = UUID("30000000-0000-4000-8000-000000000702")
VISIBLE_ASSET = UUID("40000000-0000-4000-8000-000000000701")
OUTSIDE_ASSET = UUID("40000000-0000-4000-8000-000000000702")
INACTIVE_ASSET = UUID("40000000-0000-4000-8000-000000000703")
TEXT_OVERLAY = UUID("50000000-0000-4000-8000-000000000701")
VISIBLE_SIGNATURE = UUID("50000000-0000-4000-8000-000000000702")
OUTSIDE_SIGNATURE = UUID("50000000-0000-4000-8000-000000000703")
INACTIVE_SIGNATURE = UUID("50000000-0000-4000-8000-000000000704")
EXPORT_ID = UUID("70000000-0000-4000-8000-000000000701")
OPERATION_IDS = (
    UUID("60000000-0000-4000-8000-000000000701"),
    UUID("60000000-0000-4000-8000-000000000702"),
    UUID("60000000-0000-4000-8000-000000000703"),
)


def _box(x: int, y: int, width: int, height: int) -> dict[str, object]:
    return {
        "coordinate_space": "normalized_page_millionths_v1",
        "x": x,
        "y": y,
        "width": width,
        "height": height,
    }


def _asset(asset_id: UUID, digest: str) -> dict[str, object]:
    return {
        "asset_id": str(asset_id),
        "kind": "signature_image",
        "storage_name": f"{asset_id}.bin",
        "media_type": "image/png",
        "byte_size": 67,
        "sha256": digest,
    }


def _signature(
    overlay_id: UUID,
    page_id: UUID,
    asset_id: UUID,
    *,
    box: dict[str, object],
    z_index: int,
) -> dict[str, object]:
    return {
        "overlay_id": str(overlay_id),
        "overlay_version": 1,
        "page_id": str(page_id),
        "box": box,
        "z_index": z_index,
        "payload": {
            "kind": "signature_image",
            "asset_id": str(asset_id),
            "terminology": SIGNATURE_IMAGE_TERMINOLOGY,
        },
    }


def _operation(sequence: int, payload: dict[str, object]) -> dict[str, object]:
    return {
        "operation_id": str(OPERATION_IDS[sequence - 1]),
        "operation_version": 1,
        "sequence": sequence,
        "payload": payload,
    }


def _document() -> dict[str, object]:
    return {
        "schema_version": 1,
        "case_id": str(CASE_ID),
        "revision": 7,
        "created_at": "2026-08-25T08:00:00Z",
        "updated_at": "2026-08-25T08:01:00Z",
        "template_ref": None,
        "sources": [
            {
                "source_id": str(SOURCE_ID),
                "display_name": "SENSITIVE SYNTHETIC LABEL.pdf",
                "storage_name": f"{SOURCE_ID}.bin",
                "detected_media_type": "application/pdf",
                "byte_size": 701,
                "page_count": 2,
                "sha256": "a" * 64,
                "signature_state": "none_detected",
                "encryption_state": "unencrypted",
                "import_state": "complete",
                "imported_at": "2026-08-25T08:00:00Z",
            }
        ],
        "pages": [
            {
                "page_id": str(ACTIVE_PAGE),
                "source_id": str(SOURCE_ID),
                "source_page_index": 0,
            },
            {
                "page_id": str(INACTIVE_PAGE),
                "source_id": str(SOURCE_ID),
                "source_page_index": 1,
            },
        ],
        "page_sequence_base": [str(ACTIVE_PAGE), str(INACTIVE_PAGE)],
        "page_sequence": [str(ACTIVE_PAGE)],
        "page_operations": [
            _operation(
                1,
                {
                    "kind": "rotate",
                    "page_id": str(ACTIVE_PAGE),
                    "quarter_turns_clockwise": 1,
                },
            ),
            _operation(
                2,
                {
                    "kind": "crop",
                    "page_id": str(ACTIVE_PAGE),
                    "box": _box(100_000, 100_000, 700_000, 700_000),
                },
            ),
            _operation(
                3,
                {
                    "kind": "change_sequence",
                    "reason": "remove",
                    "before_page_ids": [str(ACTIVE_PAGE), str(INACTIVE_PAGE)],
                    "after_page_ids": [str(ACTIVE_PAGE)],
                },
            ),
        ],
        "assets": [
            _asset(VISIBLE_ASSET, "b" * 64),
            _asset(OUTSIDE_ASSET, "c" * 64),
            _asset(INACTIVE_ASSET, "d" * 64),
        ],
        "overlays": [
            {
                "overlay_id": str(TEXT_OVERLAY),
                "overlay_version": 1,
                "page_id": str(ACTIVE_PAGE),
                "box": _box(200_000, 200_000, 200_000, 100_000),
                "z_index": 2,
                "payload": {
                    "kind": "text",
                    "value": "PRIVATE FORM VALUE",
                    "font_token": "paperworkshell-default",
                    "font_size_points": 12,
                    "color": "#000000ff",
                },
            },
            _signature(
                VISIBLE_SIGNATURE,
                ACTIVE_PAGE,
                VISIBLE_ASSET,
                box=_box(50_000, 50_000, 200_000, 200_000),
                z_index=5,
            ),
            _signature(
                OUTSIDE_SIGNATURE,
                ACTIVE_PAGE,
                OUTSIDE_ASSET,
                box=_box(850_000, 850_000, 100_000, 100_000),
                z_index=8,
            ),
            _signature(
                INACTIVE_SIGNATURE,
                INACTIVE_PAGE,
                INACTIVE_ASSET,
                box=_box(200_000, 200_000, 200_000, 200_000),
                z_index=1,
            ),
        ],
        "assignments": [],
        "constraints": {
            "constraints_version": 1,
            "allowed_output_modes": ["combined_pdf"],
            "encryption_policy": "must_be_unencrypted",
        },
        "confirmations": [],
        "export_history": {"storage_name": "export-history.json", "entries": []},
    }


def _snapshot(document: dict[str, object] | None = None) -> ManifestSnapshot:
    selected = _document() if document is None else document
    return ManifestSnapshot(
        CASE_ID,
        cast(int, selected["schema_version"]),
        cast(int, selected["revision"]),
        selected,
        "e" * 64,
        "flattened_export_cas_0001",
    )


def _raster(
    asset_id: UUID = VISIBLE_ASSET,
    *,
    pixel_sha256: str = "f" * 64,
) -> OverlayRasterAsset:
    return OverlayRasterAsset(
        asset_id=asset_id,
        width_pixels=2,
        height_pixels=1,
        row_stride_bytes=8,
        byte_count=8,
        pixel_sha256=pixel_sha256,
    )


def _plan(
    document: dict[str, object] | None = None,
    rasters: Mapping[UUID, OverlayRasterAsset] | None = None,
) -> FlattenedPdfExportPlan:
    return build_flattened_pdf_export_plan(
        _snapshot(document),
        {VISIBLE_ASSET: _raster()} if rasters is None else rasters,
    )


def _assert_error(
    code: FlattenedPdfExportErrorCode,
    action: Callable[[], object],
) -> FlattenedPdfExportError:
    with pytest.raises(FlattenedPdfExportError) as raised:
        action()
    assert raised.value.code is code
    return raised.value


def test_plan_wraps_base_rotation_crop_visible_stack_and_exact_raster_binding() -> None:
    plan = _plan()

    assert plan.plan_version == FLATTENED_PDF_EXPORT_PLAN_VERSION
    assert plan.base_plan.pages[0].additional_rotation is PdfExportRotation.DEGREES_90
    assert plan.pages[0].active_crop == NormalizedPageBox(100_000, 100_000, 700_000, 700_000)
    assert tuple(record.overlay_id for record in plan.pages[0].overlays) == (
        TEXT_OVERLAY,
        VISIBLE_SIGNATURE,
    )
    assert plan.pages[0].overlays[1].box == NormalizedPageBox(50_000, 50_000, 200_000, 200_000)
    assert plan.visible_overlay_count == 2
    assert plan.overlay_page_count == 1
    assert plan.text_overlay_count == 1
    assert plan.placed_signature_image_count == 1
    assert tuple(asset.asset_id for asset in plan.assets) == (VISIBLE_ASSET,)
    assert plan.assets[0].raster_asset == _raster()
    assert not plan.contains_signature_markers


def test_ordered_subset_reindexes_pages_sources_and_visible_assets_by_first_use() -> None:
    document = copy.deepcopy(_document())
    operations = cast(list[dict[str, object]], document["page_operations"])
    operations.pop()
    document["page_sequence"] = [str(ACTIVE_PAGE), str(INACTIVE_PAGE)]
    sources = cast(list[dict[str, object]], document["sources"])
    sources[0]["page_count"] = 1
    second_source = copy.deepcopy(sources[0])
    second_source.update(
        {
            "source_id": str(SECOND_SOURCE_ID),
            "storage_name": f"{SECOND_SOURCE_ID}.bin",
            "byte_size": 702,
            "page_count": 1,
            "sha256": "9" * 64,
        }
    )
    sources.append(second_source)
    pages = cast(list[dict[str, object]], document["pages"])
    pages[1]["source_id"] = str(SECOND_SOURCE_ID)
    pages[1]["source_page_index"] = 0

    plan = build_flattened_pdf_export_plan_for_pages(
        _snapshot(document),
        (INACTIVE_PAGE, ACTIVE_PAGE),
        {
            INACTIVE_ASSET: _raster(INACTIVE_ASSET),
            VISIBLE_ASSET: _raster(),
        },
    )

    assert tuple(page.base_page.output_page_index for page in plan.pages) == (0, 1)
    assert tuple(page.base_page.page_id for page in plan.pages) == (INACTIVE_PAGE, ACTIVE_PAGE)
    assert tuple(source.source_id for source in plan.base_plan.sources) == (
        SECOND_SOURCE_ID,
        SOURCE_ID,
    )
    assert tuple(asset.asset_id for asset in plan.assets) == (INACTIVE_ASSET, VISIBLE_ASSET)

    selected_only = build_flattened_pdf_export_plan_for_pages(
        _snapshot(document),
        (INACTIVE_PAGE,),
        {INACTIVE_ASSET: _raster(INACTIVE_ASSET)},
    )
    assert tuple(source.source_id for source in selected_only.base_plan.sources) == (
        SECOND_SOURCE_ID,
    )
    assert tuple(asset.asset_id for asset in selected_only.assets) == (INACTIVE_ASSET,)


def test_ordered_subset_rejects_empty_duplicate_unknown_and_inactive_pages() -> None:
    _assert_error(
        FlattenedPdfExportErrorCode.PLAN_EMPTY,
        lambda: build_flattened_pdf_export_plan_for_pages(_snapshot(), (), {}),
    )
    _assert_error(
        FlattenedPdfExportErrorCode.MODEL_INVALID,
        lambda: build_flattened_pdf_export_plan_for_pages(
            _snapshot(),
            (ACTIVE_PAGE, ACTIVE_PAGE),
            {VISIBLE_ASSET: _raster()},
        ),
    )
    _assert_error(
        FlattenedPdfExportErrorCode.SOURCE_INCONSISTENT,
        lambda: build_flattened_pdf_export_plan_for_pages(
            _snapshot(),
            (UUID("30000000-0000-4000-8000-000000000799"),),
            {},
        ),
    )
    _assert_error(
        FlattenedPdfExportErrorCode.SOURCE_INCONSISTENT,
        lambda: build_flattened_pdf_export_plan_for_pages(
            _snapshot(),
            (INACTIVE_PAGE,),
            {INACTIVE_ASSET: _raster(INACTIVE_ASSET)},
        ),
    )


def test_ordered_subset_page_count_is_bounded_before_raster_validation() -> None:
    _assert_error(
        FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
        lambda: build_flattened_pdf_export_plan_for_pages(
            _snapshot(),
            (ACTIVE_PAGE,) * (MAX_PDF_EXPORT_PAGES + 1),
            {},
        ),
    )


def test_required_assets_omit_inactive_and_outside_crop_signature_images() -> None:
    required = required_visible_signature_assets(_snapshot())

    assert tuple(asset.asset_id for asset in required) == (VISIBLE_ASSET,)


def test_required_assets_for_pages_filter_and_follow_selected_page_order() -> None:
    document = copy.deepcopy(_document())
    operations = cast(list[dict[str, object]], document["page_operations"])
    operations.pop()
    document["page_sequence"] = [str(ACTIVE_PAGE), str(INACTIVE_PAGE)]

    reordered = required_visible_signature_assets_for_pages(
        _snapshot(document),
        (INACTIVE_PAGE, ACTIVE_PAGE),
    )
    assert tuple(asset.asset_id for asset in reordered) == (INACTIVE_ASSET, VISIBLE_ASSET)

    selected_only = required_visible_signature_assets_for_pages(
        _snapshot(document),
        (INACTIVE_PAGE,),
    )
    assert tuple(asset.asset_id for asset in selected_only) == (INACTIVE_ASSET,)


@pytest.mark.parametrize(
    ("ordered_page_ids", "code"),
    (
        ((), FlattenedPdfExportErrorCode.PLAN_EMPTY),
        ((ACTIVE_PAGE, ACTIVE_PAGE), FlattenedPdfExportErrorCode.MODEL_INVALID),
        (
            (UUID("30000000-0000-4000-8000-000000000799"),),
            FlattenedPdfExportErrorCode.SOURCE_INCONSISTENT,
        ),
        ((INACTIVE_PAGE,), FlattenedPdfExportErrorCode.SOURCE_INCONSISTENT),
    ),
)
def test_required_assets_for_pages_rejects_invalid_page_selection(
    ordered_page_ids: tuple[UUID, ...],
    code: FlattenedPdfExportErrorCode,
) -> None:
    _assert_error(
        code,
        lambda: required_visible_signature_assets_for_pages(_snapshot(), ordered_page_ids),
    )


@pytest.mark.parametrize("mutation", ("crop", "rotation", "text", "encoded", "raster"))
def test_every_flattened_output_semantic_change_invalidates_fingerprint(mutation: str) -> None:
    baseline = _plan()
    document = copy.deepcopy(_document())
    rasters: Mapping[UUID, OverlayRasterAsset] = {VISIBLE_ASSET: _raster()}
    if mutation == "crop":
        operations = cast(list[dict[str, object]], document["page_operations"])
        cast(dict[str, object], operations[1]["payload"])["box"] = _box(
            110_000, 100_000, 690_000, 700_000
        )
    elif mutation == "rotation":
        operations = cast(list[dict[str, object]], document["page_operations"])
        cast(dict[str, object], operations[0]["payload"])["quarter_turns_clockwise"] = 2
    elif mutation == "text":
        overlays = cast(list[dict[str, object]], document["overlays"])
        cast(dict[str, object], overlays[0]["payload"])["value"] = "CHANGED SYNTHETIC"
    elif mutation == "encoded":
        assets = cast(list[dict[str, object]], document["assets"])
        assets[0]["sha256"] = "9" * 64
    else:
        rasters = {VISIBLE_ASSET: _raster(pixel_sha256="8" * 64)}

    assert _plan(document, rasters).semantic_fingerprint != baseline.semantic_fingerprint


def test_inactive_and_outside_crop_overlay_changes_do_not_change_output_fingerprint() -> None:
    baseline = _plan()
    document = copy.deepcopy(_document())
    overlays = cast(list[dict[str, object]], document["overlays"])
    cast(dict[str, object], overlays[2]["box"])["x"] = 880_000
    cast(dict[str, object], overlays[3]["box"])["x"] = 300_000

    assert _plan(document).semantic_fingerprint == baseline.semantic_fingerprint


def test_internal_overlay_and_asset_identifiers_do_not_change_semantic_fingerprint() -> None:
    baseline = _plan()
    document = copy.deepcopy(_document())
    replacement_asset = UUID("40000000-0000-4000-8000-000000000799")
    assets = cast(list[dict[str, object]], document["assets"])
    assets[0]["asset_id"] = str(replacement_asset)
    assets[0]["storage_name"] = f"{replacement_asset}.bin"
    overlays = cast(list[dict[str, object]], document["overlays"])
    overlays[0]["overlay_id"] = "50000000-0000-4000-8000-000000000798"
    overlays[1]["overlay_id"] = "50000000-0000-4000-8000-000000000799"
    cast(dict[str, object], overlays[1]["payload"])["asset_id"] = str(replacement_asset)
    raster = _raster(replacement_asset)

    assert _plan(document, {replacement_asset: raster}).semantic_fingerprint == (
        baseline.semantic_fingerprint
    )


@pytest.mark.parametrize("identity_kind", ("case", "source", "page", "asset"))
def test_plan_rejects_collisions_across_the_global_identity_namespace(
    identity_kind: str,
) -> None:
    plan = _plan()
    selected_identity = {
        "case": plan.base_plan.case_id,
        "source": plan.base_plan.sources[0].source_id,
        "page": plan.base_plan.pages[0].page_id,
        "asset": plan.base_plan.case_id,
    }[identity_kind]
    pages = plan.pages
    assets = plan.assets
    if identity_kind == "asset":
        signature = pages[0].overlays[1]
        pages = (
            replace(
                pages[0],
                overlays=(
                    pages[0].overlays[0],
                    replace(
                        signature,
                        payload=SignatureImageOverlayPayload(selected_identity),
                    ),
                ),
            ),
        )
        assets = (
            replace(
                assets[0],
                encoded_asset=replace(
                    assets[0].encoded_asset,
                    asset_id=selected_identity,
                    storage_name=f"{selected_identity}.bin",
                ),
                raster_asset=replace(
                    assets[0].raster_asset,
                    asset_id=selected_identity,
                ),
            ),
        )
    else:
        pages = (
            replace(
                pages[0],
                overlays=(
                    replace(pages[0].overlays[0], overlay_id=selected_identity),
                    pages[0].overlays[1],
                ),
            ),
        )

    error = _assert_error(
        FlattenedPdfExportErrorCode.MODEL_INVALID,
        lambda: FlattenedPdfExportPlan(
            base_plan=plan.base_plan,
            pages=pages,
            assets=assets,
        ),
    )
    assert error.context == "flattened.plan_identities"


def test_manifest_projection_rejects_a_flattened_asset_colliding_with_a_source() -> None:
    document = copy.deepcopy(_document())
    assets = cast(list[dict[str, object]], document["assets"])
    assets[0]["asset_id"] = str(SOURCE_ID)
    assets[0]["storage_name"] = f"{SOURCE_ID}.bin"
    overlays = cast(list[dict[str, object]], document["overlays"])
    cast(dict[str, object], overlays[1]["payload"])["asset_id"] = str(SOURCE_ID)

    error = _assert_error(
        FlattenedPdfExportErrorCode.MANIFEST_INVALID,
        lambda: _plan(document, {}),
    )
    assert error.context == "flattened.manifest"


def test_missing_extra_and_malformed_raster_metadata_fail_closed() -> None:
    _assert_error(
        FlattenedPdfExportErrorCode.RASTER_METADATA_MISSING,
        lambda: _plan(rasters={}),
    )
    _assert_error(
        FlattenedPdfExportErrorCode.RASTER_METADATA_EXTRA,
        lambda: _plan(
            rasters={
                VISIBLE_ASSET: _raster(),
                OUTSIDE_ASSET: _raster(OUTSIDE_ASSET),
            }
        ),
    )
    _assert_error(
        FlattenedPdfExportErrorCode.RASTER_METADATA_INVALID,
        lambda: _plan(rasters={VISIBLE_ASSET: _raster(OUTSIDE_ASSET)}),
    )


def test_report_has_exact_limited_checks_and_signature_image_terminology() -> None:
    plan = _plan()
    report = FlattenedPdfExportVerificationReport.for_verified_plan(
        plan,
        output_sha256="7" * 64,
        output_byte_size=707,
    )

    assert report.base_report.semantic_fingerprint == plan.semantic_fingerprint
    assert not report.base_report.unsigned_derivative
    assert tuple(check.code for check in report.checks) == (
        FlattenedPdfExportCheckCode.VISIBLE_OVERLAYS_FLATTENED,
        FlattenedPdfExportCheckCode.OVERLAY_PRESENCE_MATCHED,
        FlattenedPdfExportCheckCode.TEXT_FONT_POLICY_MATCHED,
        FlattenedPdfExportCheckCode.SIGNATURE_IMAGE_TERMINOLOGY_PRESERVED,
    )
    signature_claim = report.checks[-1].claim
    assert "placed signature images" in signature_claim
    assert "did not create a digital signature" in signature_claim
    joined = " ".join(report.claims).lower()
    assert "portal acceptance" not in joined
    assert "legally valid" not in joined
    assert "pdf/ua" not in joined

    _assert_error(
        FlattenedPdfExportErrorCode.REPORT_INVALID,
        lambda: replace(report, checks=tuple(reversed(report.checks))),
    )


def test_bad_signature_terminology_and_representations_do_not_disclose_values() -> None:
    document = _document()
    overlays = cast(list[dict[str, object]], document["overlays"])
    cast(dict[str, object], overlays[1]["payload"])["terminology"] = "digitally_signed"
    error = _assert_error(
        FlattenedPdfExportErrorCode.MANIFEST_INVALID,
        lambda: _plan(document),
    )

    plan = _plan()
    report = FlattenedPdfExportVerificationReport.for_verified_plan(
        plan,
        output_sha256="7" * 64,
        output_byte_size=707,
    )
    rendered = " ".join((repr(plan), repr(plan.pages[0]), repr(plan.assets[0]), repr(report)))
    assert "PRIVATE FORM VALUE" not in rendered
    assert "SENSITIVE SYNTHETIC LABEL" not in rendered
    assert "f" * 64 not in rendered
    assert "digitally_signed" not in repr(error)
    assert "digitally_signed" not in str(error)
    assert error.__cause__ is None
    assert error.__context__ is None


def test_outcome_retains_flattened_checks_while_history_uses_base_report() -> None:
    report = FlattenedPdfExportVerificationReport.for_verified_plan(
        _plan(),
        output_sha256="7" * 64,
        output_byte_size=707,
    )
    payload = b'{"synthetic":"flattened history"}\n'
    receipt = PdfExportHistoryRetryReceipt(
        case_id=CASE_ID,
        export_id=EXPORT_ID,
        report=report.base_report,
        record_sha256=hashlib.sha256(payload).hexdigest(),
        record_payload=payload,
    )
    base_outcome = PdfExportOutcome(
        export_id=EXPORT_ID,
        status=PdfExportOutcomeStatus.COMMITTED_RECORDED,
        report=report.base_report,
        destination_reopened=True,
        history_retry=receipt,
    )

    outcome = FlattenedPdfExportOutcome(base_outcome, report)

    assert outcome.export_id == EXPORT_ID
    assert outcome.status is PdfExportOutcomeStatus.COMMITTED_RECORDED
    assert outcome.destination_reopened
    assert outcome.history_retry is receipt
    assert not outcome.history_retry_required
    assert not outcome.export_retry_required
    assert tuple(check.code for check in outcome.checks[-5:-1]) == tuple(
        check.code for check in report.checks
    )
    assert outcome.claims[-1] == base_outcome.checks[-1].claim
    assert outcome.safe_message == (
        "The verified flattened PDF was saved and recorded in case history."
    )

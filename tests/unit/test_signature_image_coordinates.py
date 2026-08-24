"""Normalized rotated/cropped placement evidence for PWS-0305 signature images."""

from __future__ import annotations

from uuid import UUID

import pytest

from paperworkshell.domain.page_coordinates import (
    ClockwiseRotation,
    NormalizedPageBox,
    PdfPageBoxSelection,
    PdfPageCoordinateGeometry,
    PdfUserSpaceBox,
)
from paperworkshell.overlays.models import (
    OverlayAssetKind,
    OverlayAssetMediaType,
    OverlayAssetRecord,
    OverlayCollection,
    OverlayRecord,
    OverlayVisibility,
    SignatureImageOverlayPayload,
)
from paperworkshell.overlays.rendering import (
    OverlayPageGeometry,
    OverlayPageRenderRequest,
)

PAGE_ID = UUID("30530000-0000-4000-8000-000000000001")
ASSET_ID = UUID("30530000-0000-4000-8000-000000000002")
OVERLAY_ID = UUID("30530000-0000-4000-8000-000000000003")
PLACEMENT = NormalizedPageBox(100_000, 200_000, 600_000, 400_000)
ACTIVE_CROP = NormalizedPageBox(250_000, 0, 750_000, 1_000_000)


def _collection() -> OverlayCollection:
    asset = OverlayAssetRecord(
        ASSET_ID,
        OverlayAssetKind.SIGNATURE_IMAGE,
        f"{ASSET_ID}.bin",
        OverlayAssetMediaType.PNG,
        64,
        "a" * 64,
    )
    overlay = OverlayRecord(
        OVERLAY_ID,
        PAGE_ID,
        PLACEMENT,
        0,
        SignatureImageOverlayPayload(ASSET_ID),
    )
    return OverlayCollection(
        (asset,),
        (overlay,),
        known_page_ids=(PAGE_ID,),
        active_page_ids=(PAGE_ID,),
    )


@pytest.mark.parametrize("rotation", tuple(ClockwiseRotation))
def test_signature_placement_keeps_full_page_normalized_intent_through_crop_and_rotation(
    rotation: ClockwiseRotation,
) -> None:
    pdf_geometry = PdfPageCoordinateGeometry(
        PdfUserSpaceBox(10.0, 20.0, 330.0, 440.0),
        PdfUserSpaceBox(20.0, 40.0, 320.0, 400.0),
        PdfPageBoxSelection.CROP,
        rotation,
        user_unit=2.0,
    )
    geometry = OverlayPageGeometry.from_pdf_geometry(pdf_geometry)

    request = OverlayPageRenderRequest.from_collection(
        _collection(),
        PAGE_ID,
        geometry=geometry,
        active_crop=ACTIVE_CROP,
    )

    assert request.records[0].box == PLACEMENT
    projection = request.projections[0]
    assert projection.visibility is OverlayVisibility.PARTIALLY_VISIBLE
    assert projection.visible_box == NormalizedPageBox(250_000, 200_000, 450_000, 400_000)
    requested_canvas = geometry.box_for(PLACEMENT)
    visible_canvas = geometry.box_for(projection.visible_box)
    assert requested_canvas.width / geometry.canvas_width_points == pytest.approx(0.6)
    assert requested_canvas.height / geometry.canvas_height_points == pytest.approx(0.4)
    assert visible_canvas.width / geometry.canvas_width_points == pytest.approx(0.45)
    assert visible_canvas.height / geometry.canvas_height_points == pytest.approx(0.4)

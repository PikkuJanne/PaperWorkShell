"""Synthetic flattened-export values shared by focused worker tests."""

from __future__ import annotations

from uuid import UUID

from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.pdf_export import (
    PdfExportPage,
    PdfExportPlan,
    PdfExportRotation,
    PdfExportSource,
    PdfExportSourceSignatureState,
)
from paperworkshell.overlays.exporting import (
    FlattenedPdfExportAsset,
    FlattenedPdfExportPage,
    FlattenedPdfExportPlan,
)
from paperworkshell.overlays.models import (
    OverlayAssetKind,
    OverlayAssetMediaType,
    OverlayAssetRecord,
    OverlayRecord,
    SignatureImageOverlayPayload,
)
from paperworkshell.overlays.rendering import OverlayRasterAsset


def uuid4(value: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{value:012x}")


def synthetic_flattened_plan() -> FlattenedPdfExportPlan:
    source = PdfExportSource(
        source_id=uuid4(2),
        byte_size=1_024,
        page_count=1,
        signature_state=PdfExportSourceSignatureState.NONE_DETECTED,
        sha256="1" * 64,
    )
    base_page = PdfExportPage(
        output_page_index=0,
        page_id=uuid4(3),
        source_id=source.source_id,
        source_page_index=0,
        additional_rotation=PdfExportRotation.DEGREES_0,
    )
    base_plan = PdfExportPlan(
        case_id=uuid4(1),
        manifest_revision=7,
        manifest_sha256="2" * 64,
        manifest_cas_token="abcdefghijklmnop",
        sources=(source,),
        pages=(base_page,),
    )
    encoded_asset = OverlayAssetRecord(
        asset_id=uuid4(5),
        kind=OverlayAssetKind.SIGNATURE_IMAGE,
        storage_name=f"{uuid4(5)}.bin",
        media_type=OverlayAssetMediaType.PNG,
        byte_size=128,
        sha256="3" * 64,
    )
    raster_asset = OverlayRasterAsset(
        asset_id=encoded_asset.asset_id,
        width_pixels=2,
        height_pixels=2,
        row_stride_bytes=8,
        byte_count=16,
        pixel_sha256="4" * 64,
    )
    overlay = OverlayRecord(
        overlay_id=uuid4(4),
        page_id=base_page.page_id,
        box=NormalizedPageBox(100_000, 100_000, 300_000, 200_000),
        z_index=0,
        payload=SignatureImageOverlayPayload(encoded_asset.asset_id),
    )
    return FlattenedPdfExportPlan(
        base_plan=base_plan,
        pages=(
            FlattenedPdfExportPage(
                base_page=base_page,
                active_crop=NormalizedPageBox.full_page(),
                overlays=(overlay,),
            ),
        ),
        assets=(FlattenedPdfExportAsset(encoded_asset, raster_asset),),
    )

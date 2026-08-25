"""Native Form-XObject composition tests for PWS-0307 flattened export."""

from __future__ import annotations

import hashlib
import time
import zlib
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pikepdf
import pytest

import paperworkshell.pdf.flattened_export_adapter as adapter_module
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.pdf_export import (
    PdfExportPage,
    PdfExportPlan,
    PdfExportRotation,
    PdfExportSource,
    PdfExportSourceSignatureState,
)
from paperworkshell.domain.pdf_inspection import PdfInspectionLimits, PdfStructuralState
from paperworkshell.domain.pdf_rendering import PdfPageRenderLimits
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.overlays.exporting import (
    FlattenedPdfExportAsset,
    FlattenedPdfExportError,
    FlattenedPdfExportErrorCode,
    FlattenedPdfExportPage,
    FlattenedPdfExportPlan,
)
from paperworkshell.overlays.models import (
    MarkOverlayPayload,
    OverlayAssetKind,
    OverlayAssetMediaType,
    OverlayAssetRecord,
    OverlayKind,
    OverlayRecord,
    RgbaColor,
    ShapeOverlayPayload,
    ShapePrimitive,
    SignatureImageOverlayPayload,
    TextualOverlayPayload,
)
from paperworkshell.overlays.rendering import OverlayRasterAsset, OverlayRenderLimits
from paperworkshell.overlays.signature_rasters import SignatureImageOverlayRasterSource
from paperworkshell.pdf.export_adapter import PdfExportAdapterControl, PdfExportAdapterLimits
from paperworkshell.pdf.flattened_export_adapter import FlattenedPdfExportAdapter

_ROOT = Path(__file__).resolve().parents[2]
_GEOMETRY_SOURCE = _ROOT / "tests/fixtures/synthetic/v1/pdf/render-geometry-matrix.pdf"
_TEXT_SOURCE = _ROOT / "tests/fixtures/synthetic/v1/pdf/born-digital.pdf"


def _uuid(prefix: int, index: int) -> UUID:
    return UUID(f"{prefix:08x}-0000-4000-8000-{index:012x}")


class _CleanChecker:
    def __init__(self) -> None:
        self.calls = 0

    def check(
        self,
        source_path: Path,
        *,
        limits: PdfInspectionLimits,
        deadline: float,
        cancellation_probe: object,
        monotonic: object,
    ) -> PdfStructuralState:
        assert source_path.is_absolute()
        assert type(limits) is PdfInspectionLimits
        assert deadline > 0
        assert callable(cancellation_probe)
        assert callable(monotonic)
        self.calls += 1
        return PdfStructuralState.CLEAN


def _limits() -> PdfExportAdapterLimits:
    return PdfExportAdapterLimits(
        inspection=PdfInspectionLimits(
            maximum_pages=100,
            maximum_objects=100_000,
            maximum_input_bytes=50_000_000,
            maximum_temporary_bytes=50_000_000,
            maximum_output_bytes=50_000_000,
            maximum_tool_output_bytes=1_000_000,
        ),
        rendering=PdfPageRenderLimits(
            maximum_pages=100,
            maximum_width_pixels=1_024,
            maximum_height_pixels=1_024,
            maximum_pixels=1_024 * 1_024,
            maximum_bitmap_bytes=1_024 * 1_024 * 4,
            maximum_destination_bytes=1_024 * 1_024 * 4,
            maximum_memory_bytes=64 * 1_024 * 1_024,
            wall_time_ms=60_000,
        ),
        maximum_total_source_bytes=50_000_000,
        verification_width_pixels=512,
        verification_height_pixels=512,
    )


def _control() -> PdfExportAdapterControl:
    return PdfExportAdapterControl(deadline=time.monotonic() + 60.0)


def _source(payload: bytes, source_id: UUID, page_count: int) -> PdfExportSource:
    return PdfExportSource(
        source_id=source_id,
        byte_size=len(payload),
        page_count=page_count,
        signature_state=PdfExportSourceSignatureState.NONE_DETECTED,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def _mark(page_id: UUID, index: int) -> OverlayRecord:
    return OverlayRecord(
        overlay_id=_uuid(0x63000000, index),
        page_id=page_id,
        box=NormalizedPageBox(200_000, 200_000, 240_000, 220_000),
        z_index=0,
        payload=MarkOverlayPayload(
            OverlayKind.CHECKMARK,
            RgbaColor("#0060ffff"),
        ),
    )


def _geometry_plan() -> tuple[FlattenedPdfExportPlan, bytes]:
    payload = _GEOMETRY_SOURCE.read_bytes()
    source_id = _uuid(0x61000000, 1)
    source = _source(payload, source_id, 6)
    page_specs = (
        (1, PdfExportRotation.DEGREES_90),
        (4, PdfExportRotation.DEGREES_270),
        (5, PdfExportRotation.DEGREES_180),
    )
    base_pages = tuple(
        PdfExportPage(
            output_page_index=output_index,
            page_id=_uuid(0x62000000, output_index + 1),
            source_id=source_id,
            source_page_index=source_page_index,
            additional_rotation=rotation,
        )
        for output_index, (source_page_index, rotation) in enumerate(page_specs)
    )
    base_plan = PdfExportPlan(
        case_id=_uuid(0x60000000, 1),
        manifest_revision=7,
        manifest_sha256="0" * 64,
        manifest_cas_token="flattened-adapter-cas-1",
        sources=(source,),
        pages=base_pages,
    )
    crops = (
        NormalizedPageBox(100_000, 100_000, 800_000, 750_000),
        NormalizedPageBox(100_000, 100_000, 800_000, 750_000),
        NormalizedPageBox(10_359, 203_302, 956_576, 774_865),
    )
    pages = tuple(
        FlattenedPdfExportPage(
            base_page=page,
            active_crop=crops[index],
            overlays=(_mark(page.page_id, index + 1),),
        )
        for index, page in enumerate(base_pages)
    )
    return FlattenedPdfExportPlan(base_plan=base_plan, pages=pages, assets=()), payload


def _geometry_plan_variant(
    plan: FlattenedPdfExportPlan,
    *,
    page_position: int,
    crop: NormalizedPageBox,
    additional_rotation: PdfExportRotation,
) -> FlattenedPdfExportPlan:
    base_pages = tuple(
        PdfExportPage(
            output_page_index=page.output_page_index,
            page_id=page.page_id,
            source_id=page.source_id,
            source_page_index=page.source_page_index,
            additional_rotation=(
                additional_rotation if index == page_position else page.additional_rotation
            ),
        )
        for index, page in enumerate(plan.base_plan.pages)
    )
    base_plan = PdfExportPlan(
        case_id=plan.base_plan.case_id,
        manifest_revision=plan.base_plan.manifest_revision,
        manifest_sha256=plan.base_plan.manifest_sha256,
        manifest_cas_token=plan.base_plan.manifest_cas_token,
        sources=plan.base_plan.sources,
        pages=base_pages,
    )
    pages = tuple(
        FlattenedPdfExportPage(
            base_page=base_pages[index],
            active_crop=crop if index == page_position else page.active_crop,
            overlays=page.overlays,
        )
        for index, page in enumerate(plan.pages)
    )
    return FlattenedPdfExportPlan(base_plan=base_plan, pages=pages, assets=plan.assets)


def _text_plan() -> tuple[FlattenedPdfExportPlan, bytes]:
    payload = _TEXT_SOURCE.read_bytes()
    source_id = _uuid(0x64000000, 1)
    page_id = _uuid(0x65000000, 1)
    source = _source(payload, source_id, 1)
    base_page = PdfExportPage(
        output_page_index=0,
        page_id=page_id,
        source_id=source_id,
        source_page_index=0,
        additional_rotation=PdfExportRotation.DEGREES_0,
    )
    base_plan = PdfExportPlan(
        case_id=_uuid(0x64000000, 2),
        manifest_revision=2,
        manifest_sha256="1" * 64,
        manifest_cas_token="flattened-adapter-cas-2",
        sources=(source,),
        pages=(base_page,),
    )
    record = OverlayRecord(
        overlay_id=_uuid(0x66000000, 1),
        page_id=page_id,
        box=NormalizedPageBox(100_000, 100_000, 600_000, 120_000),
        z_index=0,
        payload=TextualOverlayPayload(
            OverlayKind.TEXT,
            "Verified overlay text",
            "paperworkshell-default",
            18,
            RgbaColor("#102030ff"),
        ),
    )
    page = FlattenedPdfExportPage(
        base_page=base_page,
        active_crop=NormalizedPageBox.full_page(),
        overlays=(record,),
    )
    return FlattenedPdfExportPlan(base_plan=base_plan, pages=(page,), assets=()), payload


def _plan_with_source_payload(
    plan: FlattenedPdfExportPlan,
    payload: bytes,
) -> FlattenedPdfExportPlan:
    source = replace(
        plan.base_plan.sources[0],
        byte_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    base_plan = PdfExportPlan(
        case_id=plan.base_plan.case_id,
        manifest_revision=plan.base_plan.manifest_revision,
        manifest_sha256=plan.base_plan.manifest_sha256,
        manifest_cas_token=plan.base_plan.manifest_cas_token,
        sources=(source,),
        pages=plan.base_plan.pages,
        profile=plan.base_plan.profile,
        plan_version=plan.base_plan.plan_version,
    )
    return FlattenedPdfExportPlan(
        base_plan=base_plan,
        pages=plan.pages,
        assets=plan.assets,
        plan_version=plan.plan_version,
    )


def _large_canvas_source_payload() -> bytes:
    destination = BytesIO()
    with pikepdf.Pdf.new() as document:
        page = document.add_blank_page(page_size=(14_400, 14_400))
        page.obj[pikepdf.Name("/Contents")] = document.make_stream(b"")
        document.save(destination, deterministic_id=True, compress_streams=False)
    return destination.getvalue()


def _signature_plan() -> tuple[
    FlattenedPdfExportPlan,
    bytes,
    dict[UUID, SignatureImageOverlayRasterSource],
]:
    payload = _TEXT_SOURCE.read_bytes()
    source_id = _uuid(0x67000000, 1)
    page_id = _uuid(0x68000000, 1)
    asset_id = _uuid(0x69000000, 1)
    pixels = bytes((255, 0, 0, 255, 0, 0, 255, 128))
    raster_asset = OverlayRasterAsset(
        asset_id=asset_id,
        width_pixels=2,
        height_pixels=1,
        row_stride_bytes=8,
        byte_count=8,
        pixel_sha256=hashlib.sha256(pixels).hexdigest(),
    )
    encoded = OverlayAssetRecord(
        asset_id=asset_id,
        kind=OverlayAssetKind.SIGNATURE_IMAGE,
        storage_name=f"{asset_id}.bin",
        media_type=OverlayAssetMediaType.PNG,
        byte_size=8,
        sha256="2" * 64,
    )
    source = _source(payload, source_id, 1)
    base_page = PdfExportPage(
        output_page_index=0,
        page_id=page_id,
        source_id=source_id,
        source_page_index=0,
        additional_rotation=PdfExportRotation.DEGREES_0,
    )
    base_plan = PdfExportPlan(
        case_id=_uuid(0x67000000, 2),
        manifest_revision=3,
        manifest_sha256="3" * 64,
        manifest_cas_token="flattened-adapter-cas-3",
        sources=(source,),
        pages=(base_page,),
    )
    record = OverlayRecord(
        overlay_id=_uuid(0x6A000000, 1),
        page_id=page_id,
        box=NormalizedPageBox(200_000, 250_000, 500_000, 250_000),
        z_index=0,
        payload=SignatureImageOverlayPayload(asset_id),
    )
    plan = FlattenedPdfExportPlan(
        base_plan=base_plan,
        pages=(
            FlattenedPdfExportPage(
                base_page=base_page,
                active_crop=NormalizedPageBox.full_page(),
                overlays=(record,),
            ),
        ),
        assets=(FlattenedPdfExportAsset(encoded, raster_asset),),
    )
    return plan, payload, {asset_id: SignatureImageOverlayRasterSource(raster_asset, pixels)}


def _run(
    plan: FlattenedPdfExportPlan,
    payload: bytes,
    output: Path,
    *,
    raster_sources: dict[UUID, SignatureImageOverlayRasterSource] | None = None,
    control: PdfExportAdapterControl | None = None,
    overlay_limits: OverlayRenderLimits | None = None,
    adapter_limits: PdfExportAdapterLimits | None = None,
) -> tuple[Any, BytesIO, _CleanChecker]:
    stream = BytesIO(payload)
    stream.seek(7)
    checker = _CleanChecker()
    report = FlattenedPdfExportAdapter(qpdf_checker=checker).assemble_and_verify(
        plan,
        {plan.base_plan.sources[0].source_id: stream},
        {} if raster_sources is None else raster_sources,
        output.resolve(),
        _limits() if adapter_limits is None else adapter_limits,
        (
            OverlayRenderLimits.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1)
            if overlay_limits is None
            else overlay_limits
        ),
        _control() if control is None else control,
    )
    return report, stream, checker


def _append_unresolved_overlay_invocation(payload: bytes) -> bytes:
    output = BytesIO()
    with pikepdf.Pdf.open(
        BytesIO(payload),
        attempt_recovery=False,
        suppress_warnings=True,
    ) as document:
        document.pages[0].contents_add(
            b"q\n1 0 0 1 40 40 cm\n/PWSOverlay0 Do\nQ\n",
            prepend=False,
        )
        document.save(output, deterministic_id=True)
    return output.getvalue()


def _append_type3_latent_overlay_invocation(
    payload: bytes,
    glyph_content: bytes = b"1000 0 d0\n/PWSOverlay0 Do\n",
) -> bytes:
    output = BytesIO()
    with pikepdf.Pdf.open(
        BytesIO(payload),
        attempt_recovery=False,
        suppress_warnings=True,
    ) as document:
        page = document.pages[0]
        glyph = document.make_stream(glyph_content)
        char_procs = pikepdf.Dictionary()
        char_procs[pikepdf.Name("/A")] = glyph
        encoding = pikepdf.Dictionary()
        encoding[pikepdf.Name("/Type")] = pikepdf.Name("/Encoding")
        encoding[pikepdf.Name("/Differences")] = pikepdf.Array([65, pikepdf.Name("/A")])
        font = pikepdf.Dictionary()
        font[pikepdf.Name("/Type")] = pikepdf.Name("/Font")
        font[pikepdf.Name("/Subtype")] = pikepdf.Name("/Type3")
        font[pikepdf.Name("/Name")] = pikepdf.Name("/FT3")
        font[pikepdf.Name("/FontBBox")] = pikepdf.Array([0, 0, 1000, 1000])
        font[pikepdf.Name("/FontMatrix")] = pikepdf.Array([0.001, 0, 0, 0.001, 0, 0])
        font[pikepdf.Name("/CharProcs")] = char_procs
        font[pikepdf.Name("/Encoding")] = encoding
        font[pikepdf.Name("/FirstChar")] = 65
        font[pikepdf.Name("/LastChar")] = 65
        font[pikepdf.Name("/Widths")] = pikepdf.Array([1000])
        fonts = page.Resources.get(pikepdf.Name("/Font"), pikepdf.Dictionary())
        assert isinstance(fonts, pikepdf.Dictionary)
        fonts[pikepdf.Name("/FT3")] = document.make_indirect(font)
        page.Resources[pikepdf.Name("/Font")] = fonts
        page.contents_add(
            b"BT\n/FT3 12 Tf\n1 0 0 1 72 72 Tm\n(A) Tj\nET\n",
            prepend=False,
        )
        document.save(output, deterministic_id=True)
    return output.getvalue()


def _append_direct_page_content(payload: bytes, content: bytes) -> bytes:
    output = BytesIO()
    with pikepdf.Pdf.open(
        BytesIO(payload),
        attempt_recovery=False,
        suppress_warnings=True,
    ) as document:
        document.pages[0].contents_add(content, prepend=False)
        document.save(output, deterministic_id=True)
    return output.getvalue()


def _append_annotation_appearance_invocation(payload: bytes) -> bytes:
    output = BytesIO()
    with pikepdf.Pdf.open(
        BytesIO(payload),
        attempt_recovery=False,
        suppress_warnings=True,
    ) as document:
        page = document.pages[0]
        annotations: list[object] = []
        for index in range(128):
            benign_appearance = document.make_stream(b"q\nQ\n")
            benign_appearance[pikepdf.Name("/Type")] = pikepdf.Name("/XObject")
            benign_appearance[pikepdf.Name("/Subtype")] = pikepdf.Name("/Form")
            benign_appearance[pikepdf.Name("/BBox")] = pikepdf.Array([0, 0, 1, 1])
            benign = pikepdf.Dictionary()
            benign[pikepdf.Name("/Type")] = pikepdf.Name("/Annot")
            benign[pikepdf.Name("/Subtype")] = pikepdf.Name("/Stamp")
            benign[pikepdf.Name("/Rect")] = pikepdf.Array([index, 0, index + 1, 1])
            benign[pikepdf.Name("/AP")] = pikepdf.Dictionary(N=benign_appearance)
            annotations.append(document.make_indirect(benign))
        appearance = document.make_stream(b"q\n/PWSOverlay0 Do\nQ\n")
        appearance[pikepdf.Name("/Type")] = pikepdf.Name("/XObject")
        appearance[pikepdf.Name("/Subtype")] = pikepdf.Name("/Form")
        appearance[pikepdf.Name("/BBox")] = pikepdf.Array([0, 0, 20, 20])
        appearances = pikepdf.Dictionary()
        appearances[pikepdf.Name("/N")] = appearance
        annotation = pikepdf.Dictionary()
        annotation[pikepdf.Name("/Type")] = pikepdf.Name("/Annot")
        annotation[pikepdf.Name("/Subtype")] = pikepdf.Name("/Stamp")
        annotation[pikepdf.Name("/Rect")] = pikepdf.Array([72, 72, 92, 92])
        annotation[pikepdf.Name("/AP")] = appearances
        annotations.append(document.make_indirect(annotation))
        page.obj[pikepdf.Name("/Annots")] = pikepdf.Array(annotations)
        document.save(output, deterministic_id=True)
    return output.getvalue()


def _append_visible_source_resources(
    payload: bytes,
    *,
    compress_streams: bool = True,
    inherit_resources: bool = False,
) -> bytes:
    output = BytesIO()
    with pikepdf.Pdf.open(BytesIO(payload), attempt_recovery=False) as document:
        page = document.pages[0]
        image = document.make_stream(bytes((255, 0, 0)) * 100)
        image[pikepdf.Name("/Type")] = pikepdf.Name("/XObject")
        image[pikepdf.Name("/Subtype")] = pikepdf.Name("/Image")
        image[pikepdf.Name("/Width")] = 10
        image[pikepdf.Name("/Height")] = 10
        image[pikepdf.Name("/ColorSpace")] = pikepdf.Name("/DeviceRGB")
        image[pikepdf.Name("/BitsPerComponent")] = 8
        xobjects = page.Resources.get(pikepdf.Name("/XObject"), pikepdf.Dictionary())
        assert isinstance(xobjects, pikepdf.Dictionary)
        xobjects[pikepdf.Name("/ImSource")] = image
        page.Resources[pikepdf.Name("/XObject")] = xobjects
        graphics_state = pikepdf.Dictionary()
        graphics_state[pikepdf.Name("/Type")] = pikepdf.Name("/ExtGState")
        graphics_state[pikepdf.Name("/ca")] = 0.5
        graphics_state[pikepdf.Name("/CA")] = 0.5
        states = pikepdf.Dictionary()
        states[pikepdf.Name("/GSOURCE")] = document.make_indirect(graphics_state)
        page.Resources[pikepdf.Name("/ExtGState")] = states
        page.contents_add(
            b"q\n/GSOURCE gs\n20 0 0 20 40 40 cm\n/ImSource Do\nQ\n",
            prepend=False,
        )
        if inherit_resources:
            parent = page.obj.get(pikepdf.Name("/Parent"))
            assert isinstance(parent, pikepdf.Dictionary)
            parent[pikepdf.Name("/Resources")] = page.obj.Resources
            del page.obj[pikepdf.Name("/Resources")]
        document.save(
            output,
            deterministic_id=True,
            compress_streams=compress_streams,
        )
    return output.getvalue()


def _append_multifilter_visible_image(payload: bytes) -> bytes:
    output = BytesIO()
    with pikepdf.Pdf.open(BytesIO(payload), attempt_recovery=False) as document:
        page = document.pages[0]
        pixels = bytes((255, 0, 0)) * 100
        encoded = zlib.compress(pixels).hex().encode("ascii") + b">"
        image = document.make_stream(b"")
        image.write(
            encoded,
            filter=pikepdf.Array(
                (
                    pikepdf.Name("/ASCIIHexDecode"),
                    pikepdf.Name.FlateDecode,
                )
            ),
            decode_parms=pikepdf.Array((None, pikepdf.Dictionary())),
        )
        image[pikepdf.Name("/Type")] = pikepdf.Name("/XObject")
        image[pikepdf.Name("/Subtype")] = pikepdf.Name("/Image")
        image[pikepdf.Name("/Width")] = 10
        image[pikepdf.Name("/Height")] = 10
        image[pikepdf.Name("/ColorSpace")] = pikepdf.Name("/DeviceRGB")
        image[pikepdf.Name("/BitsPerComponent")] = 8
        xobjects = page.obj.Resources.get(pikepdf.Name("/XObject"), pikepdf.Dictionary())
        assert isinstance(xobjects, pikepdf.Dictionary)
        xobjects[pikepdf.Name("/ImMulti")] = image
        page.obj.Resources[pikepdf.Name("/XObject")] = xobjects
        page.contents_add(b"q\n100 0 0 100 40 40 cm\n/ImMulti Do\nQ\n", prepend=False)
        document.save(
            output,
            deterministic_id=True,
            compress_streams=False,
            stream_decode_level=pikepdf.StreamDecodeLevel.none,
            recompress_flate=False,
        )
    return output.getvalue()


def _append_red_stamp_annotation(
    payload: bytes,
    *,
    include_page_reference: bool = False,
) -> bytes:
    output = BytesIO()
    with pikepdf.Pdf.open(BytesIO(payload), attempt_recovery=False) as document:
        page = document.pages[0]
        appearance = document.make_stream(b"q\n1 0 0 rg\n0 0 20 20 re\nf\nQ\n")
        appearance[pikepdf.Name("/Type")] = pikepdf.Name("/XObject")
        appearance[pikepdf.Name("/Subtype")] = pikepdf.Name("/Form")
        appearance[pikepdf.Name("/BBox")] = pikepdf.Array((0, 0, 20, 20))
        appearance[pikepdf.Name("/Resources")] = pikepdf.Dictionary()
        appearances = pikepdf.Dictionary()
        appearances[pikepdf.Name("/N")] = appearance
        annotation = pikepdf.Dictionary()
        annotation[pikepdf.Name("/Type")] = pikepdf.Name("/Annot")
        annotation[pikepdf.Name("/Subtype")] = pikepdf.Name("/Stamp")
        annotation[pikepdf.Name("/Rect")] = pikepdf.Array((72, 72, 92, 92))
        annotation[pikepdf.Name("/AP")] = appearances
        if include_page_reference:
            annotation[pikepdf.Name("/P")] = page.obj
        page.obj[pikepdf.Name("/Annots")] = pikepdf.Array((document.make_indirect(annotation),))
        document.save(output, deterministic_id=True)
    return output.getvalue()


def _append_optional_content(
    payload: bytes,
    *,
    location: str,
    default_on: bool,
) -> bytes:
    output = BytesIO()
    with pikepdf.Pdf.open(BytesIO(payload), attempt_recovery=False) as document:
        page = document.pages[0]
        ocg = document.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name("/OCG"),
                Name=pikepdf.String("Synthetic optional layer"),
            )
        )
        default = pikepdf.Dictionary(BaseState=pikepdf.Name("/ON"))
        default[pikepdf.Name("/ON" if default_on else "/OFF")] = pikepdf.Array((ocg,))
        document.Root[pikepdf.Name("/OCProperties")] = pikepdf.Dictionary(
            OCGs=pikepdf.Array((ocg,)),
            D=default,
        )
        properties = pikepdf.Dictionary(OC1=ocg)
        optional_paint = b"/OC /OC1 BDC\n1 0 0 rg\n0 0 100 100 re\nf\nEMC\n"
        if location == "page":
            page.obj.Resources[pikepdf.Name("/Properties")] = properties
            page.contents_add(optional_paint, prepend=False)
        elif location == "form":
            form = document.make_stream(b"1 0 0 rg\n0 0 100 100 re\nf\n")
            form[pikepdf.Name("/Type")] = pikepdf.Name("/XObject")
            form[pikepdf.Name("/Subtype")] = pikepdf.Name("/Form")
            form[pikepdf.Name("/BBox")] = pikepdf.Array((0, 0, 100, 100))
            form[pikepdf.Name("/Resources")] = pikepdf.Dictionary()
            form[pikepdf.Name("/OC")] = ocg
            page.obj.Resources[pikepdf.Name("/XObject")] = pikepdf.Dictionary(FOC=form)
            page.contents_add(b"q\n/FOC Do\nQ\n", prepend=False)
        elif location == "annotation":
            annotations: list[object] = []
            for index in range(128):
                benign_appearance = document.make_stream(b"q\nQ\n")
                benign_appearance[pikepdf.Name("/Type")] = pikepdf.Name("/XObject")
                benign_appearance[pikepdf.Name("/Subtype")] = pikepdf.Name("/Form")
                benign_appearance[pikepdf.Name("/BBox")] = pikepdf.Array((0, 0, 1, 1))
                benign_appearance[pikepdf.Name("/Resources")] = pikepdf.Dictionary()
                benign = pikepdf.Dictionary(
                    Type=pikepdf.Name("/Annot"),
                    Subtype=pikepdf.Name("/Stamp"),
                    Rect=pikepdf.Array((index, 0, index + 1, 1)),
                    AP=pikepdf.Dictionary(N=benign_appearance),
                )
                annotations.append(document.make_indirect(benign))
            appearance = document.make_stream(optional_paint)
            appearance[pikepdf.Name("/Type")] = pikepdf.Name("/XObject")
            appearance[pikepdf.Name("/Subtype")] = pikepdf.Name("/Form")
            appearance[pikepdf.Name("/BBox")] = pikepdf.Array((0, 0, 100, 100))
            appearance[pikepdf.Name("/Resources")] = pikepdf.Dictionary(
                Properties=properties,
            )
            annotation = pikepdf.Dictionary(
                Type=pikepdf.Name("/Annot"),
                Subtype=pikepdf.Name("/Stamp"),
                Rect=pikepdf.Array((0, 0, 100, 100)),
                AP=pikepdf.Dictionary(N=appearance),
            )
            annotations.append(document.make_indirect(annotation))
            page.obj[pikepdf.Name("/Annots")] = pikepdf.Array(annotations)
        else:
            raise AssertionError(location)
        document.save(output, deterministic_id=True)
    return output.getvalue()


def _overlay_form(page: pikepdf.Page) -> pikepdf.Stream:
    xobjects = page.obj.Resources.XObject
    names = [name for name in xobjects.keys() if str(name).startswith("/PWSOverlay")]
    assert len(names) == 1
    form = xobjects[names[0]]
    assert isinstance(form, pikepdf.Stream)
    return form


def test_rotated_cropped_mixed_size_and_user_unit_pages_flatten_forms(
    tmp_path: Path,
) -> None:
    plan, payload = _geometry_plan()
    output = tmp_path / "flattened-geometry.pdf"

    report, stream, checker = _run(plan, payload, output)

    assert report.plan is plan
    assert report.base_report.page_count == 3
    assert checker.calls == 1
    assert stream.tell() == 7
    with pikepdf.Pdf.open(output, attempt_recovery=False) as document:
        assert [int(page.obj.Rotate) for page in document.pages] == [180, 270, 180]
        assert [float(page.obj.UserUnit) for page in document.pages] == [1.0, 1.0, 2.0]
        for page in document.pages:
            form = _overlay_form(page)
            assert str(form.Type) == "/XObject"
            assert str(form.Subtype) == "/Form"
            assert pikepdf.Name("/Matrix") not in form
            contents = page.obj.Contents
            assert isinstance(contents, pikepdf.Array)
            assert contents[0].read_bytes() == b"q\n"
            assert b" cm\n/PWSOverlay0 Do\nQ\n" in contents[-1].read_bytes()


@pytest.mark.parametrize("page_position", (0, 1, 2))
@pytest.mark.parametrize("additional_rotation", tuple(PdfExportRotation))
def test_valid_crop_user_unit_and_rotation_matrix_survives_pdfium_float32_boundary(
    tmp_path: Path,
    page_position: int,
    additional_rotation: PdfExportRotation,
) -> None:
    plan, payload = _geometry_plan()
    crops = (
        NormalizedPageBox(10_359, 203_302, 956_576, 774_865),
        NormalizedPageBox(76_543, 12_345, 812_345, 900_001),
        NormalizedPageBox(33_333, 111_111, 900_001, 800_000),
    )
    selected = _geometry_plan_variant(
        plan,
        page_position=page_position,
        crop=crops[page_position],
        additional_rotation=additional_rotation,
    )
    output = tmp_path / f"crop-{page_position}-{int(additional_rotation)}.pdf"

    report, _stream, _checker = _run(selected, payload, output)

    assert report.base_report.page_count == 3
    with pikepdf.Pdf.open(output, attempt_recovery=False) as document:
        assert len(document.pages) == 3
        assert all(_overlay_form(page) is not None for page in document.pages)


def test_composed_text_retains_embedded_type0_font_and_extractable_actual_text(
    tmp_path: Path,
) -> None:
    plan, payload = _text_plan()
    output = tmp_path / "flattened-text.pdf"

    report, _stream, _checker = _run(plan, payload, output)

    assert report.plan.text_overlay_count == 1
    with pikepdf.Pdf.open(output, attempt_recovery=False) as document:
        form = _overlay_form(document.pages[0])
        fonts = form.Resources.Font
        assert len(fonts) == 1
        font = fonts[next(iter(fonts.keys()))]
        assert str(font.Subtype) == "/Type0"
        assert pikepdf.Name("/ToUnicode") in font
        descendant = font.DescendantFonts[0]
        assert str(descendant.Subtype) == "/CIDFontType2"
        assert pikepdf.Name("/FontFile2") in descendant.FontDescriptor


def test_existing_unresolved_invocation_cannot_activate_new_overlay_form(
    tmp_path: Path,
) -> None:
    plan, original_payload = _text_plan()
    payload = _append_unresolved_overlay_invocation(original_payload)
    source = replace(
        plan.base_plan.sources[0],
        byte_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    base_plan = PdfExportPlan(
        case_id=plan.base_plan.case_id,
        manifest_revision=plan.base_plan.manifest_revision,
        manifest_sha256=plan.base_plan.manifest_sha256,
        manifest_cas_token=plan.base_plan.manifest_cas_token,
        sources=(source,),
        pages=plan.base_plan.pages,
        profile=plan.base_plan.profile,
        plan_version=plan.base_plan.plan_version,
    )
    plan = FlattenedPdfExportPlan(
        base_plan=base_plan,
        pages=plan.pages,
        assets=plan.assets,
        plan_version=plan.plan_version,
    )
    output = tmp_path / "reserved-invocation-name.pdf"

    _run(plan, payload, output)

    with pikepdf.Pdf.open(output, attempt_recovery=False) as document:
        page = document.pages[0]
        xobjects = page.obj.Resources.XObject
        assert pikepdf.Name("/PWSOverlay0") not in xobjects
        assert pikepdf.Name("/PWSOverlay1") in xobjects
        invocation_names = tuple(
            instruction.operands[0]
            for instruction in pikepdf.parse_content_stream(page, operators="Do")
        )
        assert invocation_names.count(pikepdf.Name("/PWSOverlay0")) == 1
        assert invocation_names.count(pikepdf.Name("/PWSOverlay1")) == 1


def test_type3_charproc_invocation_cannot_activate_new_overlay_form(
    tmp_path: Path,
) -> None:
    plan, original_payload = _text_plan()
    payload = _append_type3_latent_overlay_invocation(original_payload)
    source = replace(
        plan.base_plan.sources[0],
        byte_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    base_plan = PdfExportPlan(
        case_id=plan.base_plan.case_id,
        manifest_revision=plan.base_plan.manifest_revision,
        manifest_sha256=plan.base_plan.manifest_sha256,
        manifest_cas_token=plan.base_plan.manifest_cas_token,
        sources=(source,),
        pages=plan.base_plan.pages,
        profile=plan.base_plan.profile,
        plan_version=plan.base_plan.plan_version,
    )
    plan = FlattenedPdfExportPlan(
        base_plan=base_plan,
        pages=plan.pages,
        assets=plan.assets,
        plan_version=plan.plan_version,
    )
    output = tmp_path / "reserved-type3-invocation-name.pdf"

    _run(plan, payload, output)

    with pikepdf.Pdf.open(output, attempt_recovery=False) as document:
        page = document.pages[0]
        xobjects = page.obj.Resources.XObject
        assert pikepdf.Name("/PWSOverlay0") not in xobjects
        assert pikepdf.Name("/PWSOverlay1") in xobjects
        glyph = page.obj.Resources.Font[pikepdf.Name("/FT3")].CharProcs[pikepdf.Name("/A")]
        assert isinstance(glyph, pikepdf.Stream)
        assert glyph.read_bytes() == b"1000 0 d0\n/PWSOverlay0 Do\n"


def test_annotation_appearance_invocation_cannot_activate_new_overlay_form(
    tmp_path: Path,
) -> None:
    plan, original_payload = _text_plan()
    payload = _append_annotation_appearance_invocation(original_payload)
    source = replace(
        plan.base_plan.sources[0],
        byte_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    base_plan = PdfExportPlan(
        case_id=plan.base_plan.case_id,
        manifest_revision=plan.base_plan.manifest_revision,
        manifest_sha256=plan.base_plan.manifest_sha256,
        manifest_cas_token=plan.base_plan.manifest_cas_token,
        sources=(source,),
        pages=plan.base_plan.pages,
        profile=plan.base_plan.profile,
        plan_version=plan.base_plan.plan_version,
    )
    selected = FlattenedPdfExportPlan(
        base_plan=base_plan,
        pages=plan.pages,
        assets=plan.assets,
        plan_version=plan.plan_version,
    )
    output = tmp_path / "reserved-annotation-invocation-name.pdf"

    _run(selected, payload, output)

    with pikepdf.Pdf.open(output, attempt_recovery=False) as document:
        page = document.pages[0]
        xobjects = page.obj.Resources.XObject
        assert pikepdf.Name("/PWSOverlay0") not in xobjects
        assert pikepdf.Name("/PWSOverlay1") in xobjects
        assert len(page.obj.Annots) == 129
        appearance = page.obj.Annots[-1].AP.N
        assert isinstance(appearance, pikepdf.Stream)
        assert appearance.read_bytes() == b"q\n/PWSOverlay0 Do\nQ\n"


def test_malformed_type3_charproc_fails_closed(tmp_path: Path) -> None:
    plan, original_payload = _text_plan()
    payload = _append_type3_latent_overlay_invocation(
        original_payload,
        b"1000 0 d0\n(unterminated",
    )
    source = replace(
        plan.base_plan.sources[0],
        byte_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    base_plan = PdfExportPlan(
        case_id=plan.base_plan.case_id,
        manifest_revision=plan.base_plan.manifest_revision,
        manifest_sha256=plan.base_plan.manifest_sha256,
        manifest_cas_token=plan.base_plan.manifest_cas_token,
        sources=(source,),
        pages=plan.base_plan.pages,
        profile=plan.base_plan.profile,
        plan_version=plan.base_plan.plan_version,
    )
    selected = FlattenedPdfExportPlan(
        base_plan=base_plan,
        pages=plan.pages,
        assets=plan.assets,
        plan_version=plan.plan_version,
    )
    output = tmp_path / "malformed-type3-content.pdf"

    with pytest.raises(FlattenedPdfExportError) as raised:
        _run(selected, payload, output)

    assert raised.value.code is FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED
    assert raised.value.context == "flattened.source_content"
    assert not output.exists()


def test_resource_graph_decoded_byte_limit_fails_closed() -> None:
    _plan, payload = _text_plan()
    with pikepdf.Pdf.open(BytesIO(payload), attempt_recovery=False) as document:
        with pytest.raises(FlattenedPdfExportError) as raised:
            adapter_module._existing_xobject_invocation_names(
                document.pages[0],
                100_000,
                1,
                1,
                _control(),
            )

    assert raised.value.code is FlattenedPdfExportErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == "flattened.source_content_bytes"


def test_large_opaque_image_stream_does_not_consume_content_decode_budget() -> None:
    with pikepdf.Pdf.new() as document:
        page = document.add_blank_page(page_size=(20, 20))
        page.obj[pikepdf.Name("/Contents")] = document.make_stream(b"")
        image = document.make_stream(b"\x80" * 1_000_000)
        image[pikepdf.Name("/Type")] = pikepdf.Name("/XObject")
        image[pikepdf.Name("/Subtype")] = pikepdf.Name("/Image")
        image[pikepdf.Name("/Width")] = 500
        image[pikepdf.Name("/Height")] = 500
        image[pikepdf.Name("/ColorSpace")] = pikepdf.Name("/DeviceRGB")
        image[pikepdf.Name("/BitsPerComponent")] = 8
        page.obj.Resources[pikepdf.Name("/XObject")] = pikepdf.Dictionary(
            Im0=image,
        )

        names = adapter_module._existing_xobject_invocation_names(
            page,
            100_000,
            1,
            1,
            _control(),
        )

    assert names == frozenset()


def test_source_resource_fingerprint_hashes_large_opaque_stream_raw() -> None:
    with pikepdf.Pdf.new() as document:
        page = document.add_blank_page(page_size=(20, 20))
        image = document.make_stream(b"")
        image.write(
            zlib.compress(b"\x80" * 1_000_000),
            filter=pikepdf.Name("/FlateDecode"),
        )
        image[pikepdf.Name("/Type")] = pikepdf.Name("/XObject")
        image[pikepdf.Name("/Subtype")] = pikepdf.Name("/Image")
        image[pikepdf.Name("/Width")] = 500
        image[pikepdf.Name("/Height")] = 500
        image[pikepdf.Name("/ColorSpace")] = pikepdf.Name("/DeviceRGB")
        image[pikepdf.Name("/BitsPerComponent")] = 8
        page.obj.Resources[pikepdf.Name("/XObject")] = pikepdf.Dictionary(Im0=image)

        fingerprint = adapter_module._source_resource_fingerprint(
            page.obj.Resources,
            excluded_xobject_name=None,
            preserve_empty_page_xobjects=True,
            maximum_objects=100,
            maximum_bytes=10_000,
            chunk_bytes=127,
            control=_control(),
            failure_code=FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
            context="test.source_resources",
        )

    assert len(fingerprint) == 64


def test_source_copy_materializes_inherited_resources_and_preserves_raw_image(
    tmp_path: Path,
) -> None:
    plan, payload = _text_plan()
    payload = _append_visible_source_resources(
        payload,
        compress_streams=False,
        inherit_resources=True,
    )
    selected = _plan_with_source_payload(plan, payload)
    with pikepdf.Pdf.open(
        BytesIO(payload),
        attempt_recovery=False,
        inherit_page_attributes=False,
    ) as source:
        page = source.pages[0]
        assert pikepdf.Name("/Resources") not in page.obj
        parent = page.obj.Parent
        assert pikepdf.Name("/ImSource") in parent.Resources.XObject
        assert pikepdf.Name("/Filter") not in parent.Resources.XObject.ImSource
    output = tmp_path / "inherited-uncompressed-resources.pdf"

    report, _stream, _checker = _run(selected, payload, output)

    assert report.base_report.page_count == 1
    with pikepdf.Pdf.open(output, attempt_recovery=False) as document:
        resources = document.pages[0].obj.Resources
        assert pikepdf.Name("/Font") in resources
        image = resources.XObject.ImSource
        assert pikepdf.Name("/Filter") not in image
        assert image.read_bytes() == bytes((255, 0, 0)) * 100


def test_source_copy_preserves_multifilter_opaque_image_representation(
    tmp_path: Path,
) -> None:
    plan, payload = _text_plan()
    payload = _append_multifilter_visible_image(payload)
    selected = _plan_with_source_payload(plan, payload)
    with pikepdf.Pdf.open(
        BytesIO(payload),
        attempt_recovery=False,
        inherit_page_attributes=False,
    ) as source:
        source_image = source.pages[0].obj.Resources.XObject.ImMulti
        source_raw = bytes(source_image.get_raw_stream_buffer())
        assert tuple(map(str, source_image.Filter)) == (
            "/ASCIIHexDecode",
            "/FlateDecode",
        )
        assert source_image.read_bytes() == bytes((255, 0, 0)) * 100
    output = tmp_path / "multifilter-source-image.pdf"

    report, _stream, _checker = _run(selected, payload, output)

    assert report.base_report.page_count == 1
    with pikepdf.Pdf.open(output, attempt_recovery=False) as document:
        image = document.pages[0].obj.Resources.XObject.ImMulti
        assert tuple(map(str, image.Filter)) == (
            "/ASCIIHexDecode",
            "/FlateDecode",
        )
        assert bytes(image.get_raw_stream_buffer()) == source_raw
        assert image.read_bytes() == bytes((255, 0, 0)) * 100


def test_representation_preserving_candidate_still_enforces_output_bound(
    tmp_path: Path,
) -> None:
    plan, payload = _text_plan()
    payload = _append_multifilter_visible_image(payload)
    selected = _plan_with_source_payload(plan, payload)
    default_limits = _limits()
    limited = replace(
        default_limits,
        inspection=replace(default_limits.inspection, maximum_output_bytes=4_096),
    )
    output = tmp_path / "representation-output-limit.pdf"

    with pytest.raises(FlattenedPdfExportError) as raised:
        _run(selected, payload, output, adapter_limits=limited)

    assert raised.value.code is FlattenedPdfExportErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == "flattened.output_bytes"
    assert not output.exists()


def test_inherited_resource_resolver_rejects_malformed_cycle_and_depth_limit() -> None:
    with pikepdf.Pdf.new() as document:
        malformed = document.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name("/Page")))
        malformed[pikepdf.Name("/Resources")] = pikepdf.Name("/Invalid")
        with pytest.raises(FlattenedPdfExportError) as invalid:
            adapter_module._resolve_inherited_page_resources(
                malformed,
                10,
                _control(),
            )

        first = document.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name("/Page")))
        second = document.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name("/Pages")))
        third = document.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name("/Pages")))
        first[pikepdf.Name("/Parent")] = second
        second[pikepdf.Name("/Parent")] = third
        third[pikepdf.Name("/Parent")] = second
        with pytest.raises(FlattenedPdfExportError) as cycle:
            adapter_module._resolve_inherited_page_resources(first, 10, _control())

        depth_page = document.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name("/Page")))
        depth_parent = document.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name("/Pages")))
        depth_root = document.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name("/Pages")))
        depth_root[pikepdf.Name("/Resources")] = pikepdf.Dictionary()
        depth_page[pikepdf.Name("/Parent")] = depth_parent
        depth_parent[pikepdf.Name("/Parent")] = depth_root
        with pytest.raises(FlattenedPdfExportError) as exhausted:
            adapter_module._resolve_inherited_page_resources(depth_page, 2, _control())

        orphan = document.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name("/Page")))
        with pytest.raises(FlattenedPdfExportError) as orphaned:
            adapter_module._resolve_inherited_page_resources(orphan, 10, _control())

        wrong_root = document.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name("/Catalog")))
        with pytest.raises(FlattenedPdfExportError) as wrong:
            adapter_module._resolve_inherited_page_resources(wrong_root, 10, _control())

        empty_root = document.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name("/Pages")))
        empty_page = document.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name("/Page")))
        empty_page[pikepdf.Name("/Parent")] = empty_root
        empty = adapter_module._resolve_inherited_page_resources(
            empty_page,
            10,
            _control(),
        )

    assert invalid.value.code is FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED
    assert cycle.value.code is FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED
    assert exhausted.value.code is FlattenedPdfExportErrorCode.LIMIT_EXCEEDED
    assert orphaned.value.code is FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED
    assert wrong.value.code is FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED
    assert isinstance(empty, pikepdf.Dictionary)
    assert not empty


def test_page_tree_without_resources_remains_exportable(tmp_path: Path) -> None:
    plan, _payload = _text_plan()
    destination = BytesIO()
    with pikepdf.Pdf.new() as document:
        page = document.add_blank_page(page_size=(612, 792))
        page.obj[pikepdf.Name("/Contents")] = document.make_stream(
            b"q\n0 0 1 rg\n20 20 40 40 re\nf\nQ\n"
        )
        del page.obj[pikepdf.Name("/Resources")]
        parent = page.obj.Parent
        if pikepdf.Name("/Resources") in parent:
            del parent[pikepdf.Name("/Resources")]
        document.save(destination, deterministic_id=True, compress_streams=False)
    payload = destination.getvalue()
    selected = _plan_with_source_payload(plan, payload)
    output = tmp_path / "resource-free-vector-page.pdf"

    report, _stream, _checker = _run(selected, payload, output)

    assert report.base_report.page_count == 1
    assert output.is_file()


@pytest.mark.parametrize(
    ("case_id", "content"),
    (
        ("leaked_ctm", b"q\n0.001 0 0 0.001 0 0 cm\nq\n"),
        ("graphics_underflow", b"Q\n"),
        ("text_unclosed", b"BT\n"),
        ("text_underflow", b"ET\n"),
        ("text_nested", b"BT\nBT\nET\nET\n"),
        ("marked_unclosed", b"/OC /Missing BDC\n"),
        ("marked_underflow", b"EMC\n"),
        ("compatibility_unclosed", b"BX\n"),
        ("compatibility_underflow", b"EX\n"),
        ("state_operand_ambiguity", b"1 q\nQ\n"),
        ("unterminated_string", b"(unterminated Tj\n"),
        ("unterminated_inline_image", b"BI /W 1 /H 1 /CS /RGB /BPC 8 ID \x00\x00\x00\n"),
    ),
)
def test_direct_page_state_cannot_escape_overlay_isolation(
    tmp_path: Path,
    case_id: str,
    content: bytes,
) -> None:
    plan, original_payload = _text_plan()
    payload = _append_direct_page_content(original_payload, content)
    source = replace(
        plan.base_plan.sources[0],
        byte_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    base_plan = PdfExportPlan(
        case_id=plan.base_plan.case_id,
        manifest_revision=plan.base_plan.manifest_revision,
        manifest_sha256=plan.base_plan.manifest_sha256,
        manifest_cas_token=plan.base_plan.manifest_cas_token,
        sources=(source,),
        pages=plan.base_plan.pages,
        profile=plan.base_plan.profile,
        plan_version=plan.base_plan.plan_version,
    )
    selected = FlattenedPdfExportPlan(
        base_plan=base_plan,
        pages=plan.pages,
        assets=plan.assets,
        plan_version=plan.plan_version,
    )
    output = tmp_path / f"state-{case_id}.pdf"

    with pytest.raises(FlattenedPdfExportError) as raised:
        _run(selected, payload, output)

    assert raised.value.code is FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED
    assert raised.value.context == "flattened.source_content_state"
    assert not output.exists()


def test_balanced_direct_page_state_remains_exportable(tmp_path: Path) -> None:
    plan, original_payload = _text_plan()
    payload = _append_direct_page_content(
        original_payload,
        b"q\nQ\nBT\nET\n/Span BMC\nEMC\n/Artifact <<>> BDC\nEMC\nBX\nEX\n",
    )
    source = replace(
        plan.base_plan.sources[0],
        byte_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    base_plan = PdfExportPlan(
        case_id=plan.base_plan.case_id,
        manifest_revision=plan.base_plan.manifest_revision,
        manifest_sha256=plan.base_plan.manifest_sha256,
        manifest_cas_token=plan.base_plan.manifest_cas_token,
        sources=(source,),
        pages=plan.base_plan.pages,
        profile=plan.base_plan.profile,
        plan_version=plan.base_plan.plan_version,
    )
    selected = FlattenedPdfExportPlan(
        base_plan=base_plan,
        pages=plan.pages,
        assets=plan.assets,
        plan_version=plan.plan_version,
    )
    output = tmp_path / "balanced-state.pdf"

    report, _stream, _checker = _run(selected, payload, output)

    assert report.base_report.page_count == 1
    assert output.is_file()


@pytest.mark.parametrize(
    ("location", "default_on"),
    (
        ("page", False),
        ("page", True),
        ("form", False),
        ("annotation", False),
    ),
)
def test_catalog_dependent_optional_content_is_rejected(
    tmp_path: Path,
    location: str,
    default_on: bool,
) -> None:
    plan, original_payload = _text_plan()
    payload = _append_optional_content(
        original_payload,
        location=location,
        default_on=default_on,
    )
    selected = _plan_with_source_payload(plan, payload)
    output = tmp_path / f"optional-content-{location}-{default_on}.pdf"

    with pytest.raises(FlattenedPdfExportError) as raised:
        _run(selected, payload, output)

    assert raised.value.code is FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED
    assert raised.value.context == "flattened.source_optional_content"
    assert not output.exists()


def test_reopen_rejects_post_save_middle_content_state_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, payload = _text_plan()
    output = tmp_path / "post-save-source-state-tamper.pdf"
    original_measure = adapter_module._measure_output
    state = {"mutated": False}

    def mutate_then_measure(*args: object, **kwargs: object) -> object:
        path = args[0]
        assert isinstance(path, Path)
        if not state["mutated"]:
            state["mutated"] = True
            staged = BytesIO()
            with pikepdf.Pdf.open(path, attempt_recovery=False) as document:
                contents = document.pages[0].obj.Contents
                assert isinstance(contents, pikepdf.Array)
                assert len(contents) >= 3
                middle = contents[1]
                assert isinstance(middle, pikepdf.Stream)
                middle.write(middle.read_bytes() + b"\nq\n0.001 0 0 0.001 0 0 cm\nq\n")
                document.save(
                    staged,
                    deterministic_id=True,
                    compress_streams=True,
                    object_stream_mode=pikepdf.ObjectStreamMode.generate,
                    normalize_content=False,
                )
            path.write_bytes(staged.getvalue())
        return original_measure(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(adapter_module, "_measure_output", mutate_then_measure)

    with pytest.raises(FlattenedPdfExportError) as raised:
        _run(plan, payload, output)

    assert state["mutated"]
    assert raised.value.code is FlattenedPdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED
    assert raised.value.context == "flattened.output_changed"
    assert not output.exists()


def test_mixed_plan_binds_source_contents_on_page_without_overlays(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, payload = _geometry_plan()
    selected = FlattenedPdfExportPlan(
        base_plan=plan.base_plan,
        pages=(replace(plan.pages[0], overlays=()), *plan.pages[1:]),
        assets=plan.assets,
        plan_version=plan.plan_version,
    )
    original = adapter_module._place_overlay_form
    mutated = False

    def blank_unoverlaid_page(*args: object, **kwargs: object) -> object:
        nonlocal mutated
        expected = original(*args, **kwargs)  # type: ignore[arg-type]
        if not mutated:
            mutated = True
            output_document = args[0]
            assert isinstance(output_document, pikepdf.Pdf)
            output_document.pages[0].obj.Contents = output_document.make_stream(b"")
        return expected

    monkeypatch.setattr(adapter_module, "_place_overlay_form", blank_unoverlaid_page)
    output = tmp_path / "mixed-unoverlaid-content-blank.pdf"

    with pytest.raises(FlattenedPdfExportError) as raised:
        _run(selected, payload, output)

    assert mutated
    assert raised.value.code is FlattenedPdfExportErrorCode.VERIFICATION_OVERLAY_PRESENCE_FAILED
    assert raised.value.context == "flattened.source_content_fingerprint"
    assert not output.exists()


def test_reopen_rejects_post_save_media_box_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, payload = _text_plan()
    output = tmp_path / "post-save-media-box-tamper.pdf"
    original_measure = adapter_module._measure_output
    mutated = False

    def mutate_then_measure(*args: object, **kwargs: object) -> object:
        nonlocal mutated
        path = args[0]
        assert isinstance(path, Path)
        if not mutated:
            mutated = True
            staged = BytesIO()
            with pikepdf.Pdf.open(path, attempt_recovery=False) as document:
                document.pages[0].obj.MediaBox = pikepdf.Array((0, 0, 50, 792))
                document.save(
                    staged,
                    deterministic_id=True,
                    compress_streams=True,
                    object_stream_mode=pikepdf.ObjectStreamMode.generate,
                    normalize_content=False,
                )
            path.write_bytes(staged.getvalue())
        return original_measure(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(adapter_module, "_measure_output", mutate_then_measure)

    with pytest.raises(FlattenedPdfExportError) as raised:
        _run(plan, payload, output)

    assert mutated
    assert raised.value.code is FlattenedPdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED
    assert raised.value.context == "flattened.output_changed"
    assert not output.exists()


def test_structural_reopen_binds_exact_media_box_after_initial_measurement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, payload = _text_plan()
    output = tmp_path / "reopen-media-box-tamper.pdf"
    original_verify = adapter_module._verify_candidate_structure

    def mutate_then_verify(*args: object, **kwargs: object) -> None:
        path = args[0]
        assert isinstance(path, Path)
        staged = BytesIO()
        with pikepdf.Pdf.open(path, attempt_recovery=False) as document:
            document.pages[0].obj.MediaBox = pikepdf.Array((0, 0, 50, 792))
            document.save(
                staged,
                deterministic_id=True,
                compress_streams=True,
                object_stream_mode=pikepdf.ObjectStreamMode.generate,
                normalize_content=False,
            )
        path.write_bytes(staged.getvalue())
        original_verify(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(adapter_module, "_verify_candidate_structure", mutate_then_verify)

    with pytest.raises(FlattenedPdfExportError) as raised:
        _run(plan, payload, output)

    assert raised.value.code is FlattenedPdfExportErrorCode.VERIFICATION_OVERLAY_PRESENCE_FAILED
    assert raised.value.context == "flattened.page_geometry"
    assert not output.exists()


@pytest.mark.parametrize("fault", ("font", "xobject", "extgstate"))
def test_reopen_binds_complete_ordinary_source_resource_graph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    plan, payload = _text_plan()
    if fault != "font":
        payload = _append_visible_source_resources(payload)
        plan = _plan_with_source_payload(plan, payload)
    original = adapter_module._place_overlay_form

    def mutate_source_resources(*args: object, **kwargs: object) -> object:
        expected = original(*args, **kwargs)  # type: ignore[arg-type]
        page = args[1]
        assert isinstance(page, pikepdf.Page)
        resources = page.obj.Resources
        if fault == "font":
            fonts = resources.Font
            first_name = next(iter(fonts.keys()))
            replacement = pikepdf.Dictionary()
            replacement[pikepdf.Name("/Type")] = pikepdf.Name("/Font")
            replacement[pikepdf.Name("/Subtype")] = pikepdf.Name("/Type1")
            replacement[pikepdf.Name("/BaseFont")] = pikepdf.Name("/Courier")
            fonts[first_name] = replacement
        elif fault == "xobject":
            del resources.XObject[pikepdf.Name("/ImSource")]
        else:
            del resources.ExtGState[pikepdf.Name("/GSOURCE")]
        return expected

    monkeypatch.setattr(adapter_module, "_place_overlay_form", mutate_source_resources)
    output = tmp_path / f"source-resource-{fault}.pdf"

    with pytest.raises(FlattenedPdfExportError) as raised:
        _run(plan, payload, output)

    assert raised.value.code is FlattenedPdfExportErrorCode.VERIFICATION_OVERLAY_PRESENCE_FAILED
    assert raised.value.context == "flattened.source_resource_fingerprint"
    assert not output.exists()


def test_reopen_binds_unfiltered_opaque_source_stream_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, payload = _text_plan()
    payload = _append_visible_source_resources(payload, compress_streams=False)
    selected = _plan_with_source_payload(plan, payload)
    original = adapter_module._place_overlay_form

    def mutate_unfiltered_image(*args: object, **kwargs: object) -> object:
        expected = original(*args, **kwargs)  # type: ignore[arg-type]
        page = args[1]
        assert isinstance(page, pikepdf.Page)
        image = page.obj.Resources.XObject.ImSource
        assert pikepdf.Name("/Filter") not in image
        changed = bytes((0, 0, 255)) * 100
        image.write(changed)
        return expected

    monkeypatch.setattr(adapter_module, "_place_overlay_form", mutate_unfiltered_image)
    output = tmp_path / "unfiltered-source-image-mutated.pdf"

    with pytest.raises(FlattenedPdfExportError) as raised:
        _run(selected, payload, output)

    assert raised.value.code is FlattenedPdfExportErrorCode.VERIFICATION_OVERLAY_PRESENCE_FAILED
    assert raised.value.context == "flattened.source_resource_fingerprint"
    assert not output.exists()


def test_reopen_binds_source_annotation_and_appearance_graph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, payload = _text_plan()
    payload = _append_red_stamp_annotation(payload)
    plan = _plan_with_source_payload(plan, payload)
    original = adapter_module._place_overlay_form

    def remove_source_annotation(*args: object, **kwargs: object) -> object:
        expected = original(*args, **kwargs)  # type: ignore[arg-type]
        page = args[1]
        assert isinstance(page, pikepdf.Page)
        del page.obj[pikepdf.Name("/Annots")]
        return expected

    monkeypatch.setattr(adapter_module, "_place_overlay_form", remove_source_annotation)
    output = tmp_path / "source-annotation-removed.pdf"

    with pytest.raises(FlattenedPdfExportError) as raised:
        _run(plan, payload, output)

    assert raised.value.code is FlattenedPdfExportErrorCode.VERIFICATION_OVERLAY_PRESENCE_FAILED
    assert raised.value.context == "flattened.source_page_fingerprint"
    assert not output.exists()


def test_annotation_page_back_reference_is_stable_and_appearance_remains_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, payload = _text_plan()
    payload = _append_red_stamp_annotation(payload, include_page_reference=True)
    selected = _plan_with_source_payload(plan, payload)
    baseline = tmp_path / "annotation-page-back-reference.pdf"

    report, _stream, _checker = _run(selected, payload, baseline)

    assert report.base_report.page_count == 1
    with pikepdf.Pdf.open(baseline, attempt_recovery=False) as document:
        annotation = document.pages[0].obj.Annots[0]
        assert annotation.P == document.pages[0].obj
        assert isinstance(annotation.AP.N, pikepdf.Stream)

    original = adapter_module._place_overlay_form

    def replace_appearance(*args: object, **kwargs: object) -> object:
        expected = original(*args, **kwargs)  # type: ignore[arg-type]
        page = args[1]
        assert isinstance(page, pikepdf.Page)
        page.obj.Annots[0].AP.N.write(b"q\n0 0 1 rg\n0 0 20 20 re\nf\nQ\n")
        return expected

    monkeypatch.setattr(adapter_module, "_place_overlay_form", replace_appearance)
    tampered = tmp_path / "annotation-page-back-reference-tampered.pdf"

    with pytest.raises(FlattenedPdfExportError) as raised:
        _run(selected, payload, tampered)

    assert raised.value.code is FlattenedPdfExportErrorCode.VERIFICATION_OVERLAY_PRESENCE_FAILED
    assert raised.value.context == "flattened.source_page_fingerprint"
    assert not tampered.exists()


def test_signature_raster_is_exact_and_report_preserves_visual_only_terminology(
    tmp_path: Path,
) -> None:
    plan, payload, rasters = _signature_plan()
    output = tmp_path / "flattened-signature.pdf"

    report, _stream, _checker = _run(
        plan,
        payload,
        output,
        raster_sources=rasters,
    )

    assert report.plan.placed_signature_image_count == 1
    assert any("did not create a digital signature" in claim for claim in report.claims)
    with pikepdf.Pdf.open(output, attempt_recovery=False) as document:
        form = _overlay_form(document.pages[0])
        assert len(form.Resources.XObject) == 1
        xobjects = form.Resources.XObject
        image = xobjects[next(iter(xobjects.keys()))]
        assert str(image.Subtype) == "/Image"


@pytest.mark.parametrize(
    "fault",
    ("missing", "displaced", "duplicate", "blank", "nested_font"),
)
def test_reopen_presence_verification_rejects_missing_displaced_or_duplicate_form(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    plan, payload = _text_plan()
    output = tmp_path / f"fault-{fault}.pdf"
    original = adapter_module._place_overlay_form

    def corrupt(*args: object, **kwargs: object) -> object:
        expected = cast(Any, original(*args, **kwargs))  # type: ignore[arg-type]
        page = args[1]
        assert isinstance(page, pikepdf.Page)
        if fault == "missing":
            del page.obj.Resources.XObject[expected.resource_name]
        elif fault == "displaced":
            contents = page.obj.Contents
            assert isinstance(contents, pikepdf.Array)
            contents[-1].write(
                expected.invocation.replace(
                    pikepdf.Matrix(*adapter_module._matrix_values(expected.placement)).encode(),
                    b"1 0 0 1 999 999",
                )
            )
        elif fault == "duplicate":
            contents = page.obj.Contents
            assert isinstance(contents, pikepdf.Array)
            contents[-1].write(expected.invocation + expected.invocation)
        elif fault == "blank":
            form = page.obj.Resources.XObject[expected.resource_name]
            assert isinstance(form, pikepdf.Stream)
            form.write(b"")
        else:
            form = page.obj.Resources.XObject[expected.resource_name]
            assert isinstance(form, pikepdf.Stream)
            fonts = form.Resources.Font
            font = fonts[next(iter(fonts.keys()))]
            font_file = font.DescendantFonts[0].FontDescriptor.FontFile2
            assert isinstance(font_file, pikepdf.Stream)
            font_file.write(b"")
        return expected

    monkeypatch.setattr(adapter_module, "_place_overlay_form", corrupt)

    with pytest.raises(FlattenedPdfExportError) as raised:
        _run(plan, payload, output)

    assert raised.value.code is FlattenedPdfExportErrorCode.VERIFICATION_OVERLAY_PRESENCE_FAILED
    if fault in {"blank", "nested_font"}:
        assert raised.value.context == "flattened.form_fingerprint"
    assert not output.exists()


def test_effective_overlay_clipped_to_zero_area_fails_rendered_semantic_oracle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, payload = _text_plan()
    output = tmp_path / "zero-area-clipped-form.pdf"
    original = adapter_module._render_overlay_page

    def clipped_generated_form(*args: object, **kwargs: object) -> object:
        data, result = original(*args, **kwargs)  # type: ignore[arg-type]
        clipped_destination = BytesIO()
        with pikepdf.Pdf.open(BytesIO(data), attempt_recovery=False) as document:
            document.pages[0].contents_add(b"q\n0 0 0 0 re\nW\nn\n", prepend=True)
            document.save(clipped_destination, deterministic_id=True)
        clipped = clipped_destination.getvalue()
        return clipped, replace(
            result,
            bytes_written=len(clipped),
            pdf_sha256=hashlib.sha256(clipped).hexdigest(),
        )

    monkeypatch.setattr(adapter_module, "_render_overlay_page", clipped_generated_form)
    monkeypatch.setattr(adapter_module, "_require_overlay_wrapper_determinism", lambda *a: None)

    with pytest.raises(FlattenedPdfExportError) as raised:
        _run(plan, payload, output)

    assert raised.value.code is FlattenedPdfExportErrorCode.ASSEMBLY_FAILED
    assert raised.value.context == "flattened.overlay_semantics"
    assert not output.exists()


@pytest.mark.parametrize("fault", ("blank", "clip"))
def test_actual_imported_form_must_render_exactly_like_renderer_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    plan, payload = _text_plan()
    output = tmp_path / f"actual-form-{fault}.pdf"
    original = adapter_module._normalize_imported_form_stream
    normalization_calls = 0

    def corrupt_imported_form(stream: pikepdf.Stream, maximum_bytes: int) -> None:
        nonlocal normalization_calls
        normalization_calls += 1
        original(stream, maximum_bytes)
        if normalization_calls != 2:
            return
        if fault == "blank":
            stream.write(b"")
        else:
            stream.write(b"q\n0 0 0 0 re\nW\nn\n" + stream.read_bytes() + b"\nQ\n")
        original(stream, maximum_bytes)

    monkeypatch.setattr(
        adapter_module,
        "_normalize_imported_form_stream",
        corrupt_imported_form,
    )

    with pytest.raises(FlattenedPdfExportError) as raised:
        _run(plan, payload, output)

    assert raised.value.code is FlattenedPdfExportErrorCode.ASSEMBLY_FAILED
    assert raised.value.context == "flattened.form_copy_fingerprint"
    assert normalization_calls == 2
    assert not output.exists()


def test_overlay_wrapper_mutation_is_rejected_by_independent_exact_rerender(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, payload = _text_plan()
    output = tmp_path / "wrapper-mutation.pdf"
    original = adapter_module._render_overlay_page

    def mutate_first_wrapper_result(*args: object, **kwargs: object) -> object:
        data, result = original(*args, **kwargs)  # type: ignore[arg-type]
        destination = BytesIO()
        with pikepdf.Pdf.open(BytesIO(data), attempt_recovery=False) as document:
            document.pages[0].contents_add(b"q\n-1 0 0 1 612 0 cm\n", prepend=True)
            document.pages[0].contents_add(b"Q\n", prepend=False)
            document.save(destination, deterministic_id=True)
        mutated = destination.getvalue()
        return mutated, replace(
            result,
            bytes_written=len(mutated),
            pdf_sha256=hashlib.sha256(mutated).hexdigest(),
        )

    monkeypatch.setattr(adapter_module, "_render_overlay_page", mutate_first_wrapper_result)

    with pytest.raises(FlattenedPdfExportError) as raised:
        _run(plan, payload, output)

    assert raised.value.code is FlattenedPdfExportErrorCode.RESULT_INVALID
    assert raised.value.context == "flattened.overlay_determinism"
    assert not output.exists()


def test_effective_overlay_cannot_claim_missing_painted_box(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, payload = _text_plan()
    output = tmp_path / "missing-painted-box.pdf"
    original = adapter_module._render_overlay_page

    def missing_painted_box(*args: object, **kwargs: object) -> object:
        data, result = original(*args, **kwargs)  # type: ignore[arg-type]
        blank_destination = BytesIO()
        with pikepdf.Pdf.open(BytesIO(data), attempt_recovery=False) as document:
            document.pages[0].obj.Contents = document.make_stream(b"q\nQ\n")
            document.save(blank_destination, deterministic_id=True)
        blank = blank_destination.getvalue()
        placements = tuple(replace(placement, painted_box=None) for placement in result.placements)
        return blank, replace(
            result,
            placements=placements,
            bytes_written=len(blank),
            pdf_sha256=hashlib.sha256(blank).hexdigest(),
        )

    monkeypatch.setattr(adapter_module, "_render_overlay_page", missing_painted_box)
    monkeypatch.setattr(adapter_module, "_require_overlay_wrapper_determinism", lambda *a: None)

    with pytest.raises(FlattenedPdfExportError) as raised:
        _run(plan, payload, output)

    assert raised.value.code is FlattenedPdfExportErrorCode.RESULT_INVALID
    assert raised.value.context == "flattened.overlay_placement"
    assert not output.exists()


def test_each_effective_placement_must_paint_its_request_region(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, payload = _text_plan()
    page = plan.pages[0]
    page_id = page.base_page.page_id
    marks = (
        OverlayRecord(
            overlay_id=_uuid(0x6C000000, 1),
            page_id=page_id,
            box=NormalizedPageBox(100_000, 200_000, 100_000, 100_000),
            z_index=0,
            payload=ShapeOverlayPayload(
                OverlayKind.SHAPE,
                ShapePrimitive.RECTANGLE,
                RgbaColor("#ff0000ff"),
                RgbaColor("#00000000"),
            ),
        ),
        OverlayRecord(
            overlay_id=_uuid(0x6C000000, 2),
            page_id=page_id,
            box=NormalizedPageBox(700_000, 200_000, 100_000, 100_000),
            z_index=1,
            payload=ShapeOverlayPayload(
                OverlayKind.SHAPE,
                ShapePrimitive.RECTANGLE,
                RgbaColor("#0000ffff"),
                RgbaColor("#00000000"),
            ),
        ),
    )
    selected = FlattenedPdfExportPlan(
        base_plan=plan.base_plan,
        pages=(replace(page, overlays=marks),),
        assets=plan.assets,
        plan_version=plan.plan_version,
    )
    original = adapter_module._render_overlay_page

    def hide_right_placement(*args: object, **kwargs: object) -> object:
        data, result = original(*args, **kwargs)  # type: ignore[arg-type]
        clipped_destination = BytesIO()
        half_width = adapter_module._canonical_pdf_number(result.canvas_width_points / 2.0)
        height = adapter_module._canonical_pdf_number(result.canvas_height_points)
        clip = b"q\n0 0 " + half_width + b" " + height + b" re\nW\nn\n"
        with pikepdf.Pdf.open(BytesIO(data), attempt_recovery=False) as document:
            document.pages[0].contents_add(clip, prepend=True)
            document.save(clipped_destination, deterministic_id=True)
        clipped = clipped_destination.getvalue()
        return clipped, replace(
            result,
            bytes_written=len(clipped),
            pdf_sha256=hashlib.sha256(clipped).hexdigest(),
        )

    monkeypatch.setattr(adapter_module, "_render_overlay_page", hide_right_placement)
    monkeypatch.setattr(adapter_module, "_require_overlay_wrapper_determinism", lambda *a: None)
    output = tmp_path / "missing-right-placement.pdf"

    with pytest.raises(FlattenedPdfExportError) as raised:
        _run(selected, payload, output)

    assert raised.value.code is FlattenedPdfExportErrorCode.ASSEMBLY_FAILED
    assert raised.value.context == "flattened.overlay_semantics"
    assert not output.exists()


def test_subpixel_effective_region_preserves_valid_coordinate_surface(
    tmp_path: Path,
) -> None:
    plan, payload = _text_plan()
    page = plan.pages[0]
    record = replace(
        page.overlays[0],
        box=NormalizedPageBox(999_000, 999_000, 1_000, 1_000),
        payload=ShapeOverlayPayload(
            OverlayKind.SHAPE,
            ShapePrimitive.RECTANGLE,
            RgbaColor("#102030ff"),
            RgbaColor("#00000000"),
        ),
    )
    selected = FlattenedPdfExportPlan(
        base_plan=plan.base_plan,
        pages=(replace(page, overlays=(record,)),),
        assets=plan.assets,
        plan_version=plan.plan_version,
    )
    output = tmp_path / "subpixel-overlay.pdf"

    report, _stream, _checker = _run(selected, payload, output)

    assert report.base_report.page_count == 1
    assert output.is_file()


def test_subpixel_effective_region_rejects_clipped_actual_form(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, payload = _text_plan()
    page = plan.pages[0]
    record = replace(
        page.overlays[0],
        box=NormalizedPageBox(999_000, 999_000, 1_000, 1_000),
        payload=ShapeOverlayPayload(
            OverlayKind.SHAPE,
            ShapePrimitive.RECTANGLE,
            RgbaColor("#102030ff"),
            RgbaColor("#00000000"),
        ),
    )
    selected = FlattenedPdfExportPlan(
        base_plan=plan.base_plan,
        pages=(replace(page, overlays=(record,)),),
        assets=plan.assets,
        plan_version=plan.plan_version,
    )
    original = adapter_module._normalize_imported_form_stream
    normalization_calls = 0

    def clip_imported_form(stream: pikepdf.Stream, maximum_bytes: int) -> None:
        nonlocal normalization_calls
        normalization_calls += 1
        original(stream, maximum_bytes)
        if normalization_calls != 2:
            return
        stream.write(b"q\n0 0 0 0 re\nW\nn\n" + stream.read_bytes() + b"\nQ\n")
        original(stream, maximum_bytes)

    monkeypatch.setattr(adapter_module, "_normalize_imported_form_stream", clip_imported_form)
    output = tmp_path / "subpixel-clipped-form.pdf"

    with pytest.raises(FlattenedPdfExportError) as raised:
        _run(selected, payload, output)

    assert raised.value.code is FlattenedPdfExportErrorCode.ASSEMBLY_FAILED
    assert raised.value.context == "flattened.form_copy_fingerprint"
    assert not output.exists()


def test_maximum_tiny_overlay_page_uses_constant_semantic_render_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, payload = _text_plan()
    page = plan.pages[0]
    records = tuple(
        OverlayRecord(
            overlay_id=_uuid(0x6D000000, index + 1),
            page_id=page.base_page.page_id,
            box=NormalizedPageBox(
                (index % 64) * 15_000,
                (index // 64) * 15_000,
                1_000,
                1_000,
            ),
            z_index=index,
            payload=ShapeOverlayPayload(
                OverlayKind.SHAPE,
                ShapePrimitive.RECTANGLE,
                RgbaColor("#102030ff"),
                RgbaColor("#00000000"),
            ),
        )
        for index in range(4_096)
    )
    selected = FlattenedPdfExportPlan(
        base_plan=plan.base_plan,
        pages=(replace(page, overlays=records),),
        assets=plan.assets,
        plan_version=plan.plan_version,
    )
    original_probe = adapter_module._render_uniform_background_probe
    probe_calls = 0

    def count_probe(*args: object, **kwargs: object) -> object:
        nonlocal probe_calls
        probe_calls += 1
        return original_probe(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(adapter_module, "_render_uniform_background_probe", count_probe)
    output = tmp_path / "maximum-tiny-overlays.pdf"

    report, _stream, _checker = _run(
        selected,
        payload,
        output,
        control=PdfExportAdapterControl(deadline=time.monotonic() + 120.0),
    )

    assert report.plan.visible_overlay_count == 4_096
    assert probe_calls == 4
    assert output.is_file()


@pytest.mark.parametrize(("value", "case_id"), (("", "empty"), (" ", "space")))
def test_no_ink_text_remains_a_valid_no_paint_placement(
    tmp_path: Path,
    value: str,
    case_id: str,
) -> None:
    plan, payload = _text_plan()
    page = plan.pages[0]
    record = page.overlays[0]
    text_payload = cast(TextualOverlayPayload, record.payload)
    selected = FlattenedPdfExportPlan(
        base_plan=plan.base_plan,
        pages=(
            replace(
                page,
                overlays=(replace(record, payload=replace(text_payload, value=value)),),
            ),
        ),
        assets=plan.assets,
        plan_version=plan.plan_version,
    )
    output = tmp_path / f"no-ink-{case_id}.pdf"

    report, _stream, _checker = _run(selected, payload, output)

    assert report.base_report.page_count == 1
    assert output.is_file()


def test_no_ink_intent_rejects_unexpected_visible_renderer_paint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, payload = _text_plan()
    page = plan.pages[0]
    record = page.overlays[0]
    text_payload = cast(TextualOverlayPayload, record.payload)
    selected = FlattenedPdfExportPlan(
        base_plan=plan.base_plan,
        pages=(
            replace(
                page,
                overlays=(replace(record, payload=replace(text_payload, value="")),),
            ),
        ),
        assets=plan.assets,
        plan_version=plan.plan_version,
    )
    original = adapter_module._render_overlay_page

    def add_unexpected_paint(*args: object, **kwargs: object) -> object:
        data, result = original(*args, **kwargs)  # type: ignore[arg-type]
        destination = BytesIO()
        with pikepdf.Pdf.open(BytesIO(data), attempt_recovery=False) as document:
            document.pages[0].contents_add(b"1 0 0 rg\n10 10 20 20 re\nf\n", prepend=False)
            document.save(destination, deterministic_id=True)
        painted = destination.getvalue()
        return painted, replace(
            result,
            bytes_written=len(painted),
            pdf_sha256=hashlib.sha256(painted).hexdigest(),
        )

    monkeypatch.setattr(adapter_module, "_render_overlay_page", add_unexpected_paint)
    monkeypatch.setattr(adapter_module, "_require_overlay_wrapper_determinism", lambda *a: None)
    output = tmp_path / "no-ink-unexpected-paint.pdf"

    with pytest.raises(FlattenedPdfExportError) as raised:
        _run(selected, payload, output)

    assert raised.value.code is FlattenedPdfExportErrorCode.ASSEMBLY_FAILED
    assert raised.value.context == "flattened.overlay_semantics"
    assert not output.exists()


@pytest.mark.parametrize(
    "transparent_payload",
    (
        MarkOverlayPayload(OverlayKind.CHECKMARK, RgbaColor("#10203000")),
        ShapeOverlayPayload(
            OverlayKind.SHAPE,
            ShapePrimitive.RECTANGLE,
            RgbaColor("#10203000"),
            RgbaColor("#40506000"),
        ),
    ),
    ids=("mark", "shape"),
)
def test_zero_alpha_vector_intent_remains_a_valid_no_paint_placement(
    tmp_path: Path,
    transparent_payload: MarkOverlayPayload | ShapeOverlayPayload,
) -> None:
    plan, payload = _text_plan()
    page = plan.pages[0]
    selected = FlattenedPdfExportPlan(
        base_plan=plan.base_plan,
        pages=(
            replace(
                page,
                overlays=(replace(page.overlays[0], payload=transparent_payload),),
            ),
        ),
        assets=plan.assets,
        plan_version=plan.plan_version,
    )
    output = tmp_path / f"transparent-{transparent_payload.kind.value}.pdf"

    report, _stream, _checker = _run(selected, payload, output)

    assert report.base_report.page_count == 1
    assert output.is_file()


@pytest.mark.parametrize(
    ("case_id", "payload"),
    (
        (
            "text",
            TextualOverlayPayload(
                OverlayKind.TEXT,
                "Alpha one text",
                "paperworkshell-default",
                18,
                RgbaColor("#10203001"),
            ),
        ),
        (
            "mark",
            MarkOverlayPayload(OverlayKind.CHECKMARK, RgbaColor("#10203001")),
        ),
        (
            "shape",
            ShapeOverlayPayload(
                OverlayKind.SHAPE,
                ShapePrimitive.RECTANGLE,
                RgbaColor("#10203001"),
                RgbaColor("#40506000"),
            ),
        ),
    ),
)
def test_alpha_one_vector_paint_is_preserved_without_unresolvable_pixel_requirement(
    tmp_path: Path,
    case_id: str,
    payload: TextualOverlayPayload | MarkOverlayPayload | ShapeOverlayPayload,
) -> None:
    plan, source_payload = _text_plan()
    page = plan.pages[0]
    selected = FlattenedPdfExportPlan(
        base_plan=plan.base_plan,
        pages=(
            replace(
                page,
                overlays=(replace(page.overlays[0], payload=payload),),
            ),
        ),
        assets=plan.assets,
        plan_version=plan.plan_version,
    )
    output = tmp_path / f"alpha-one-{case_id}.pdf"

    report, _stream, _checker = _run(selected, source_payload, output)

    assert report.base_report.page_count == 1
    with pikepdf.Pdf.open(output, attempt_recovery=False) as document:
        form = _overlay_form(document.pages[0])
        assert b" gs" in form.read_bytes()
        states = form.Resources.get(pikepdf.Name("/ExtGState"))
        assert isinstance(states, pikepdf.Dictionary)
        alpha_values = tuple(
            float(state[key])
            for state_name in states.keys()
            for state in (states[state_name],)
            for key in (pikepdf.Name("/ca"), pikepdf.Name("/CA"))
            if key in state
        )
        assert any(abs(value - 1 / 255) < 0.0001 for value in alpha_values)


def test_alpha_one_signature_is_preserved_without_unresolvable_pixel_requirement(
    tmp_path: Path,
) -> None:
    plan, payload, _rasters = _signature_plan()
    pixels = bytes((255, 0, 0, 1, 0, 0, 255, 1))
    metadata = replace(
        plan.assets[0].raster_asset,
        pixel_sha256=hashlib.sha256(pixels).hexdigest(),
    )
    selected = FlattenedPdfExportPlan(
        base_plan=plan.base_plan,
        pages=plan.pages,
        assets=(replace(plan.assets[0], raster_asset=metadata),),
        plan_version=plan.plan_version,
    )
    output = tmp_path / "alpha-one-signature.pdf"

    report, _stream, _checker = _run(
        selected,
        payload,
        output,
        raster_sources={
            metadata.asset_id: SignatureImageOverlayRasterSource(metadata, pixels),
        },
    )

    assert report.base_report.page_count == 1
    with pikepdf.Pdf.open(output, attempt_recovery=False) as document:
        form = _overlay_form(document.pages[0])
        xobjects = form.Resources.XObject
        image = xobjects[next(iter(xobjects.keys()))]
        soft_mask = image.get(pikepdf.Name("/SMask"))
        assert isinstance(soft_mask, pikepdf.Stream)
        assert b"\x01" in soft_mask.read_bytes()


def test_alpha_two_signature_requires_resolvable_pixel_hit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, payload, _rasters = _signature_plan()
    pixels = bytes((255, 0, 0, 2, 0, 0, 255, 2))
    metadata = replace(
        plan.assets[0].raster_asset,
        pixel_sha256=hashlib.sha256(pixels).hexdigest(),
    )
    selected = FlattenedPdfExportPlan(
        base_plan=plan.base_plan,
        pages=plan.pages,
        assets=(replace(plan.assets[0], raster_asset=metadata),),
        plan_version=plan.plan_version,
    )
    original_probe = adapter_module._render_uniform_background_probe

    def suppress_region_hits(*args: object, **kwargs: object) -> object:
        result, hits = original_probe(*args, **kwargs)  # type: ignore[arg-type]
        return result, tuple(False for _hit in hits)

    monkeypatch.setattr(
        adapter_module,
        "_render_uniform_background_probe",
        suppress_region_hits,
    )
    output = tmp_path / "alpha-two-signature-missing-hit.pdf"

    with pytest.raises(FlattenedPdfExportError) as raised:
        _run(
            selected,
            payload,
            output,
            raster_sources={
                metadata.asset_id: SignatureImageOverlayRasterSource(metadata, pixels),
            },
        )

    assert raised.value.code is FlattenedPdfExportErrorCode.ASSEMBLY_FAILED
    assert raised.value.context == "flattened.overlay_semantics"
    assert not output.exists()


@pytest.mark.parametrize(
    ("case_id", "payload"),
    (
        (
            "text",
            TextualOverlayPayload(
                OverlayKind.TEXT,
                "I",
                "paperworkshell-default",
                18,
                RgbaColor("#10203002"),
            ),
        ),
        (
            "check",
            MarkOverlayPayload(OverlayKind.CHECKMARK, RgbaColor("#10203002")),
        ),
        (
            "cross",
            MarkOverlayPayload(OverlayKind.CROSS, RgbaColor("#10203002")),
        ),
        (
            "line",
            ShapeOverlayPayload(
                OverlayKind.SHAPE,
                ShapePrimitive.LINE,
                RgbaColor("#00000000"),
                RgbaColor("#10203002"),
            ),
        ),
        (
            "ellipse",
            ShapeOverlayPayload(
                OverlayKind.SHAPE,
                ShapePrimitive.ELLIPSE,
                RgbaColor("#10203002"),
                RgbaColor("#00000000"),
            ),
        ),
        (
            "outline",
            ShapeOverlayPayload(
                OverlayKind.SHAPE,
                ShapePrimitive.RECTANGLE,
                RgbaColor("#00000000"),
                RgbaColor("#10203002"),
            ),
        ),
    ),
)
def test_alpha_two_sparse_paint_survives_bounded_raster_rounding(
    tmp_path: Path,
    case_id: str,
    payload: TextualOverlayPayload | MarkOverlayPayload | ShapeOverlayPayload,
) -> None:
    plan, _source_payload = _text_plan()
    source_payload = _large_canvas_source_payload()
    selected_source = _plan_with_source_payload(plan, source_payload)
    page = selected_source.pages[0]
    record = replace(
        page.overlays[0],
        box=NormalizedPageBox(499_305, 499_305, 1_389, 1_389),
        payload=payload,
    )
    selected = FlattenedPdfExportPlan(
        base_plan=selected_source.base_plan,
        pages=(replace(page, overlays=(record,)),),
        assets=selected_source.assets,
        plan_version=selected_source.plan_version,
    )
    output = tmp_path / f"alpha-two-sparse-{case_id}.pdf"

    report, _stream, _checker = _run(selected, source_payload, output)

    assert report.base_report.page_count == 1
    assert output.is_file()


def test_alpha_two_dense_fill_still_requires_resolvable_pixel_hit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, _source_payload = _text_plan()
    source_payload = _large_canvas_source_payload()
    selected_source = _plan_with_source_payload(plan, source_payload)
    page = selected_source.pages[0]
    record = replace(
        page.overlays[0],
        box=NormalizedPageBox(499_305, 499_305, 1_389, 1_389),
        payload=ShapeOverlayPayload(
            OverlayKind.SHAPE,
            ShapePrimitive.RECTANGLE,
            RgbaColor("#10203002"),
            RgbaColor("#00000000"),
        ),
    )
    selected = FlattenedPdfExportPlan(
        base_plan=selected_source.base_plan,
        pages=(replace(page, overlays=(record,)),),
        assets=selected_source.assets,
        plan_version=selected_source.plan_version,
    )
    original_probe = adapter_module._render_uniform_background_probe

    def suppress_region_hits(*args: object, **kwargs: object) -> object:
        result, hits = original_probe(*args, **kwargs)  # type: ignore[arg-type]
        return result, tuple(False for _hit in hits)

    monkeypatch.setattr(
        adapter_module,
        "_render_uniform_background_probe",
        suppress_region_hits,
    )
    output = tmp_path / "alpha-two-dense-fill-missing-hit.pdf"

    with pytest.raises(FlattenedPdfExportError) as raised:
        _run(selected, source_payload, output)

    assert raised.value.code is FlattenedPdfExportErrorCode.ASSEMBLY_FAILED
    assert raised.value.context == "flattened.overlay_semantics"
    assert not output.exists()


def test_sparse_opaque_signature_survives_bounded_raster_rounding(
    tmp_path: Path,
) -> None:
    plan, _payload, _rasters = _signature_plan()
    payload = _large_canvas_source_payload()
    selected_source = _plan_with_source_payload(plan, payload)
    mutable_pixels = bytearray(100 * 100 * 4)
    opaque_offset = (50 * 100 + 50) * 4
    mutable_pixels[opaque_offset : opaque_offset + 4] = bytes((255, 0, 0, 255))
    pixels = bytes(mutable_pixels)
    metadata = replace(
        selected_source.assets[0].raster_asset,
        width_pixels=100,
        height_pixels=100,
        row_stride_bytes=400,
        byte_count=len(pixels),
        pixel_sha256=hashlib.sha256(pixels).hexdigest(),
    )
    page = selected_source.pages[0]
    record = replace(
        page.overlays[0],
        box=NormalizedPageBox(499_305, 499_305, 1_389, 1_389),
    )
    selected = FlattenedPdfExportPlan(
        base_plan=selected_source.base_plan,
        pages=(replace(page, overlays=(record,)),),
        assets=(replace(selected_source.assets[0], raster_asset=metadata),),
        plan_version=selected_source.plan_version,
    )
    output = tmp_path / "sparse-opaque-signature.pdf"

    report, _stream, _checker = _run(
        selected,
        payload,
        output,
        raster_sources={
            metadata.asset_id: SignatureImageOverlayRasterSource(metadata, pixels),
        },
    )

    assert report.base_report.page_count == 1
    assert output.is_file()


def test_extreme_aspect_tiny_signature_uses_contained_minimum_cell(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, payload, _rasters = _signature_plan()
    pixels = bytes((255, 0, 0, 255)) * 1_000
    metadata = replace(
        plan.assets[0].raster_asset,
        width_pixels=1_000,
        height_pixels=1,
        row_stride_bytes=4_000,
        byte_count=4_000,
        pixel_sha256=hashlib.sha256(pixels).hexdigest(),
    )
    page = plan.pages[0]
    record = replace(
        page.overlays[0],
        box=NormalizedPageBox(500_000, 500_000, 1, 1),
    )
    selected = FlattenedPdfExportPlan(
        base_plan=plan.base_plan,
        pages=(replace(page, overlays=(record,)),),
        assets=(replace(plan.assets[0], raster_asset=metadata),),
        plan_version=plan.plan_version,
    )
    painted_boxes: list[Any] = []
    original = adapter_module._render_overlay_page

    def capture_painted_box(*args: object, **kwargs: object) -> object:
        data, result = original(*args, **kwargs)  # type: ignore[arg-type]
        painted_boxes.append(result.placements[0].painted_box)
        return data, result

    monkeypatch.setattr(adapter_module, "_render_overlay_page", capture_painted_box)
    output = tmp_path / "extreme-aspect-tiny-signature.pdf"

    report, _stream, _checker = _run(
        selected,
        payload,
        output,
        raster_sources={
            metadata.asset_id: SignatureImageOverlayRasterSource(metadata, pixels),
        },
    )

    assert report.base_report.page_count == 1
    assert len(painted_boxes) == 1
    painted = painted_boxes[0]
    assert painted is not None
    assert painted.width == pytest.approx(0.01)
    assert painted.height == pytest.approx(0.01)
    assert output.is_file()


def test_subcell_partial_crop_uses_clip_contained_in_requested_box(
    tmp_path: Path,
) -> None:
    plan, payload = _text_plan()
    page = plan.pages[0]
    record = replace(
        page.overlays[0],
        box=NormalizedPageBox(636_944, 799_308, 1, 15),
        payload=MarkOverlayPayload(OverlayKind.CHECKMARK, RgbaColor("#102030ff")),
    )
    selected = FlattenedPdfExportPlan(
        base_plan=plan.base_plan,
        pages=(
            replace(
                page,
                active_crop=NormalizedPageBox(0, 799_319, 1_000_000, 200_681),
                overlays=(record,),
            ),
        ),
        assets=plan.assets,
        plan_version=plan.plan_version,
    )
    output = tmp_path / "subcell-partial-crop.pdf"

    report, _stream, _checker = _run(selected, payload, output)

    assert report.base_report.page_count == 1
    assert output.is_file()


def test_all_transparent_signature_remains_valid_and_restores_raster_position(
    tmp_path: Path,
) -> None:
    plan, payload, _rasters = _signature_plan()
    transparent_pixels = bytes((255, 0, 0, 0, 0, 0, 255, 0))
    metadata = replace(
        plan.assets[0].raster_asset,
        pixel_sha256=hashlib.sha256(transparent_pixels).hexdigest(),
    )
    selected = FlattenedPdfExportPlan(
        base_plan=plan.base_plan,
        pages=plan.pages,
        assets=(replace(plan.assets[0], raster_asset=metadata),),
        plan_version=plan.plan_version,
    )
    source = SignatureImageOverlayRasterSource(metadata, transparent_pixels)
    source.seek(3)
    output = tmp_path / "transparent-signature.pdf"

    report, _stream, _checker = _run(
        selected,
        payload,
        output,
        raster_sources={metadata.asset_id: source},
    )

    assert report.base_report.page_count == 1
    assert source.tell() == 3
    assert output.is_file()


def test_signature_with_opaque_pixels_only_outside_crop_is_valid_no_paint(
    tmp_path: Path,
) -> None:
    plan, payload, _rasters = _signature_plan()
    pixels = bytes((255, 0, 0, 255, 0, 0, 255, 0))
    metadata = replace(
        plan.assets[0].raster_asset,
        pixel_sha256=hashlib.sha256(pixels).hexdigest(),
    )
    page = plan.pages[0]
    selected = FlattenedPdfExportPlan(
        base_plan=plan.base_plan,
        pages=(
            replace(
                page,
                active_crop=NormalizedPageBox(600_000, 0, 400_000, 1_000_000),
            ),
        ),
        assets=(replace(plan.assets[0], raster_asset=metadata),),
        plan_version=plan.plan_version,
    )
    source = SignatureImageOverlayRasterSource(metadata, pixels)
    source.seek(3)
    output = tmp_path / "signature-transparent-visible-crop.pdf"

    report, _stream, _checker = _run(
        selected,
        payload,
        output,
        raster_sources={metadata.asset_id: source},
    )

    assert report.base_report.page_count == 1
    assert source.tell() == 3
    assert output.is_file()


def test_signature_visible_alpha_missing_from_actual_form_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, payload, _rasters = _signature_plan()
    pixels = bytes((255, 0, 0, 0, 0, 0, 255, 255))
    metadata = replace(
        plan.assets[0].raster_asset,
        pixel_sha256=hashlib.sha256(pixels).hexdigest(),
    )
    page = plan.pages[0]
    selected = FlattenedPdfExportPlan(
        base_plan=plan.base_plan,
        pages=(
            replace(
                page,
                active_crop=NormalizedPageBox(600_000, 0, 400_000, 1_000_000),
            ),
        ),
        assets=(replace(plan.assets[0], raster_asset=metadata),),
        plan_version=plan.plan_version,
    )
    source = SignatureImageOverlayRasterSource(metadata, pixels)
    original = adapter_module._normalize_imported_form_stream

    def blank_imported_form(stream: pikepdf.Stream, maximum_bytes: int) -> None:
        original(stream, maximum_bytes)
        stream.write(b"")
        original(stream, maximum_bytes)

    monkeypatch.setattr(adapter_module, "_normalize_imported_form_stream", blank_imported_form)
    output = tmp_path / "signature-visible-alpha-missing.pdf"

    with pytest.raises(FlattenedPdfExportError) as raised:
        _run(
            selected,
            payload,
            output,
            raster_sources={metadata.asset_id: source},
        )

    assert raised.value.code is FlattenedPdfExportErrorCode.ASSEMBLY_FAILED
    assert raised.value.context == "flattened.form_render_equivalence"
    assert not output.exists()


@pytest.mark.parametrize(
    ("color", "white_changed", "black_changed"),
    (
        ("#ffffffff", False, True),
        ("#000000ff", True, False),
    ),
    ids=("white-paint", "black-paint"),
)
def test_two_background_semantic_probe_detects_white_and_black_paint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    color: str,
    white_changed: bool,
    black_changed: bool,
) -> None:
    plan, payload = _text_plan()
    page = plan.pages[0]
    selected = FlattenedPdfExportPlan(
        base_plan=plan.base_plan,
        pages=(
            replace(
                page,
                overlays=(
                    replace(
                        page.overlays[0],
                        payload=MarkOverlayPayload(
                            OverlayKind.CHECKMARK,
                            RgbaColor(color),
                        ),
                    ),
                ),
            ),
        ),
        assets=plan.assets,
        plan_version=plan.plan_version,
    )
    observed: dict[bytes, bool] = {}
    original = adapter_module._render_uniform_background_probe

    def capture_probe(*args: object, **kwargs: object) -> object:
        result = original(*args, **kwargs)  # type: ignore[arg-type]
        expected_pixel = args[2]
        assert isinstance(expected_pixel, bytes)
        observed[expected_pixel] = result[1][0]
        return result

    monkeypatch.setattr(adapter_module, "_render_uniform_background_probe", capture_probe)
    output = tmp_path / f"two-background-{color[1:7]}.pdf"

    _run(selected, payload, output)

    assert observed == {
        b"\xff\xff\xff\xff": white_changed,
        b"\x00\x00\x00\xff": black_changed,
    }


def test_uniform_background_probe_tracks_split_pixels_and_enforces_limit() -> None:
    black = b"\x00\x00\x00\xff"
    probe = adapter_module._UniformBackgroundProbe(
        8,
        black,
        2,
        1,
        (adapter_module._SemanticPixelRegion(0, 0, 2, 1),),
        1,
        _control(),
    )

    assert probe.write(b"\x00") == 1
    assert probe.write(b"\x00\x00") == 2
    assert probe.write(b"\xff\x00") == 2
    assert probe.write(b"\x00\x00\xff") == 3
    probe.flush()

    assert probe.bytes_written == 8
    assert probe.flushed
    assert probe.region_hits == (False,)
    assert probe.pixel_sha256 == hashlib.sha256(black * 2).hexdigest()

    changed = adapter_module._UniformBackgroundProbe(
        4,
        b"\xff\xff\xff\xff",
        1,
        1,
        (adapter_module._SemanticPixelRegion(0, 0, 1, 1),),
        1,
        _control(),
    )
    changed.write(b"\xff\xff")
    changed.write(b"\x00\xff")
    assert changed.region_hits == (True,)

    with pytest.raises(FlattenedPdfExportError) as raised:
        probe.write(b"\x00")

    assert raised.value.code is FlattenedPdfExportErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == "flattened.overlay_semantic_bytes"


def test_uniform_background_probe_enforces_region_comparison_bound() -> None:
    right_pixel = adapter_module._SemanticPixelRegion(1, 0, 2, 1)
    probe = adapter_module._UniformBackgroundProbe(
        8,
        b"\xff\xff\xff\xff",
        2,
        1,
        (right_pixel, right_pixel),
        1,
        _control(),
    )

    with pytest.raises(FlattenedPdfExportError) as raised:
        probe.write(b"\x00\x00\x00\xff\xff\xff\xff\xff")

    assert raised.value.code is FlattenedPdfExportErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == "flattened.overlay_semantic_comparisons"


def test_black_background_variant_uses_bounded_copy_headroom_and_fails_beyond_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, payload = _text_plan()
    captured: dict[str, object] = {}
    original = adapter_module._render_overlay_page

    def capture_overlay(*args: object, **kwargs: object) -> object:
        data, result = original(*args, **kwargs)  # type: ignore[arg-type]
        captured["data"] = data
        captured["result"] = result
        return data, result

    monkeypatch.setattr(adapter_module, "_render_overlay_page", capture_overlay)
    _run(plan, payload, tmp_path / "headroom-baseline.pdf")
    data = captured["data"]
    result = captured["result"]
    assert isinstance(data, bytes)
    limits = OverlayRenderLimits.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1)
    near_limit = replace(
        limits,
        maximum_output_bytes=len(data),
    )

    black_variant = adapter_module._black_background_overlay_pdf(
        data,
        cast(Any, result),
        near_limit,
        _control(),
    )

    assert len(black_variant.getvalue()) > len(data)
    exhausted = replace(
        OverlayRenderLimits.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1),
        maximum_output_bytes=1,
    )

    with pytest.raises(FlattenedPdfExportError) as raised:
        adapter_module._black_background_overlay_pdf(
            data,
            cast(Any, result),
            exhausted,
            _control(),
        )

    assert raised.value.code is FlattenedPdfExportErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == "flattened.overlay_semantic_pdf_bytes"


def test_form_probe_preserves_cancellation_after_bounded_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, payload = _text_plan()
    cancelled = False
    original_flush = adapter_module._BoundedMemoryPdfSink.flush

    def flush_then_cancel(sink: object) -> None:
        nonlocal cancelled
        original_flush(cast(Any, sink))
        cancelled = True

    monkeypatch.setattr(adapter_module._BoundedMemoryPdfSink, "flush", flush_then_cancel)
    control = PdfExportAdapterControl(
        deadline=time.monotonic() + 60.0,
        cancellation_probe=lambda: cancelled,
    )
    output = tmp_path / "form-probe-cancelled.pdf"

    with pytest.raises(FlattenedPdfExportError) as raised:
        _run(plan, payload, output, control=control)

    assert raised.value.code is FlattenedPdfExportErrorCode.CANCELLED
    assert not output.exists()


def test_form_probe_preserves_sanitized_fingerprint_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, payload = _text_plan()
    original = adapter_module._form_semantic_fingerprint

    def exhaust_probe_bound(*args: object, **kwargs: object) -> str:
        if kwargs.get("context") == "flattened.form_probe_fingerprint":
            raise FlattenedPdfExportError(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.form_probe_fingerprint",
            )
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(adapter_module, "_form_semantic_fingerprint", exhaust_probe_bound)
    output = tmp_path / "form-probe-bound.pdf"

    with pytest.raises(FlattenedPdfExportError) as raised:
        _run(plan, payload, output)

    assert raised.value.code is FlattenedPdfExportErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == "flattened.form_probe_fingerprint"
    assert not output.exists()


def test_semantic_pdf_sinks_are_live_serially_under_constrained_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, payload = _text_plan()

    class TrackingSink(adapter_module._BoundedMemoryPdfSink):
        active = 0
        maximum_active = 0

        def __init__(self, maximum: int) -> None:
            super().__init__(maximum)
            self.tracked_alive = True
            type(self).active += 1
            type(self).maximum_active = max(type(self).maximum_active, type(self).active)

        def close(self) -> None:
            if self.tracked_alive:
                self.tracked_alive = False
                type(self).active -= 1
            super().close()

        def __del__(self) -> None:
            if self.tracked_alive:
                type(self).active -= 1

    monkeypatch.setattr(adapter_module, "_BoundedMemoryPdfSink", TrackingSink)
    limits = replace(
        OverlayRenderLimits.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1),
        maximum_output_bytes=65_536,
    )

    _run(
        plan,
        payload,
        tmp_path / "serial-semantic-sinks.pdf",
        overlay_limits=limits,
    )

    assert TrackingSink.maximum_active == 1
    assert TrackingSink.active == 0


def test_exact_raster_mapping_rejects_extra_source_before_destination_creation(
    tmp_path: Path,
) -> None:
    plan, payload = _text_plan()
    extra_id = _uuid(0x6B000000, 1)
    pixels = b"\0\0\0\xff"
    metadata = OverlayRasterAsset(
        asset_id=extra_id,
        width_pixels=1,
        height_pixels=1,
        row_stride_bytes=4,
        byte_count=4,
        pixel_sha256=hashlib.sha256(pixels).hexdigest(),
    )
    output = tmp_path / "unexpected-raster.pdf"

    with pytest.raises(FlattenedPdfExportError) as raised:
        FlattenedPdfExportAdapter(qpdf_checker=_CleanChecker()).assemble_and_verify(
            plan,
            {plan.base_plan.sources[0].source_id: BytesIO(payload)},
            {extra_id: SignatureImageOverlayRasterSource(metadata, pixels)},
            output.resolve(),
            _limits(),
            OverlayRenderLimits.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1),
            _control(),
        )

    assert raised.value.code is FlattenedPdfExportErrorCode.RASTER_METADATA_EXTRA
    assert not output.exists()

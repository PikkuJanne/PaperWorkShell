"""Canonical path-free worker envelope for flattened visual-overlay export."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Never, cast
from uuid import UUID

from paperworkshell.domain.manifest_repository import MAX_SAFE_MANIFEST_INTEGER
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.pdf_export import MAX_PDF_EXPORT_PAGES
from paperworkshell.overlays.exporting import (
    FLATTENED_PDF_EXPORT_ASSET_VERSION,
    FLATTENED_PDF_EXPORT_PAGE_VERSION,
    FLATTENED_PDF_EXPORT_PLAN_VERSION,
    FlattenedPdfExportAsset,
    FlattenedPdfExportErrorCode,
    FlattenedPdfExportPage,
    FlattenedPdfExportPlan,
    reject_flattened_pdf_export,
)
from paperworkshell.overlays.models import MAX_OVERLAYS, OverlayAssetRecord, OverlayRecord
from paperworkshell.overlays.rendering import (
    OVERLAY_RASTER_ASSET_VERSION,
    OVERLAY_RASTER_COLOR_INTERPRETATION_V1,
    OVERLAY_RASTER_PIXEL_LAYOUT_V1,
    OverlayRasterAsset,
)
from paperworkshell.worker.pdf_export_plan import (
    PdfExportWorkerSource,
    decode_pdf_export_worker_plan,
    encode_pdf_export_worker_plan,
)
from paperworkshell.worker.protocol.models import (
    MAX_FLATTENED_PDF_EXPORT_RASTER_ASSETS,
    MAX_PDF_EXPORT_PLAN_BYTES,
)

FLATTENED_PDF_WORKER_PLAN_VERSION: Final = 1
MAX_FLATTENED_PDF_WORKER_PLAN_DEPTH: Final = 24
MAX_FLATTENED_PDF_WORKER_PLAN_NODES: Final = 131_072
MAX_FLATTENED_PDF_WORKER_PLAN_STRING_CODEPOINTS: Final = 4_096

_ROOT_MEMBERS: Final = frozenset(
    {
        "worker_plan_version",
        "plan_version",
        "semantic_fingerprint",
        "base_plan",
        "pages",
        "assets",
    }
)
_PAGE_MEMBERS: Final = frozenset(
    {
        "page_version",
        "output_page_index",
        "page_id",
        "active_crop",
        "overlays",
    }
)
_ASSET_MEMBERS: Final = frozenset({"asset_version", "encoded_asset", "raster_asset"})
_RASTER_MEMBERS: Final = frozenset(
    {
        "asset_version",
        "asset_id",
        "width_pixels",
        "height_pixels",
        "row_stride_bytes",
        "byte_count",
        "pixel_sha256",
        "pixel_layout",
        "color_interpretation",
        "worker_handle",
    }
)


class _PlanDecodeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FlattenedPdfWorkerAsset:
    """One authenticated RGBA asset paired with its reduced-right handle."""

    asset: FlattenedPdfExportAsset
    worker_handle: int = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.asset) is not FlattenedPdfExportAsset
            or type(self.worker_handle) is not int
            or not 1 <= self.worker_handle <= MAX_SAFE_MANIFEST_INTEGER
        ):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.MODEL_INVALID,
                "flattened.worker_asset",
            )


@dataclass(frozen=True, slots=True)
class FlattenedPdfWorkerPlan:
    """Decoded flattened plan with exact source and raster handle bindings."""

    plan: FlattenedPdfExportPlan
    sources: tuple[PdfExportWorkerSource, ...] = field(repr=False)
    assets: tuple[FlattenedPdfWorkerAsset, ...] = field(repr=False)
    worker_plan_version: int = FLATTENED_PDF_WORKER_PLAN_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.plan) is not FlattenedPdfExportPlan
            or type(self.sources) is not tuple
            or any(type(source) is not PdfExportWorkerSource for source in self.sources)
            or tuple(source.source for source in self.sources) != self.plan.base_plan.sources
            or type(self.assets) is not tuple
            or any(type(asset) is not FlattenedPdfWorkerAsset for asset in self.assets)
            or tuple(asset.asset for asset in self.assets) != self.plan.assets
            or type(self.worker_plan_version) is not int
            or self.worker_plan_version != FLATTENED_PDF_WORKER_PLAN_VERSION
            or len(self.assets) > MAX_FLATTENED_PDF_EXPORT_RASTER_ASSETS
        ):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.MODEL_INVALID,
                "flattened.worker_plan",
            )
        handles = tuple(source.worker_handle for source in self.sources) + tuple(
            asset.worker_handle for asset in self.assets
        )
        if len(set(handles)) != len(handles):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.MODEL_INVALID,
                "flattened.worker_handles",
            )


def encode_flattened_pdf_worker_plan(
    plan: FlattenedPdfExportPlan,
    source_handles: Mapping[UUID, int],
    raster_handles: Mapping[UUID, int],
) -> bytes:
    """Encode one canonical bounded plan after exact handle binding."""

    if (
        type(plan) is not FlattenedPdfExportPlan
        or not isinstance(source_handles, Mapping)
        or not isinstance(raster_handles, Mapping)
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.MODEL_INVALID,
            "flattened.worker_encode",
        )
    expected_asset_ids = tuple(asset.asset_id for asset in plan.assets)
    try:
        supplied_asset_ids = tuple(raster_handles.keys())
        raster_handle_values = tuple(raster_handles[asset_id] for asset_id in expected_asset_ids)
        source_handle_values = tuple(
            source_handles[source.source_id] for source in plan.base_plan.sources
        )
        base_plan = json.loads(
            encode_pdf_export_worker_plan(plan.base_plan, source_handles).decode("ascii")
        )
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.MODEL_INVALID,
            "flattened.worker_handles",
        )
    all_handles = (*source_handle_values, *raster_handle_values)
    if (
        len(plan.assets) > MAX_FLATTENED_PDF_EXPORT_RASTER_ASSETS
        or set(supplied_asset_ids) != set(expected_asset_ids)
        or len(supplied_asset_ids) != len(expected_asset_ids)
        or any(
            type(value) is not int or not 1 <= value <= MAX_SAFE_MANIFEST_INTEGER
            for value in raster_handle_values
        )
        or len(set(all_handles)) != len(all_handles)
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.MODEL_INVALID,
            "flattened.worker_handles",
        )

    document = {
        "worker_plan_version": FLATTENED_PDF_WORKER_PLAN_VERSION,
        "plan_version": plan.plan_version,
        "semantic_fingerprint": plan.semantic_fingerprint,
        "base_plan": base_plan,
        "pages": [
            {
                "page_version": page.page_version,
                "output_page_index": page.output_page_index,
                "page_id": str(page.page_id),
                "active_crop": page.active_crop.to_manifest(),
                "overlays": [overlay.to_manifest() for overlay in page.overlays],
            }
            for page in plan.pages
        ],
        "assets": [
            {
                "asset_version": asset.asset_version,
                "encoded_asset": asset.encoded_asset.to_manifest(),
                "raster_asset": {
                    "asset_version": asset.raster_asset.asset_version,
                    "asset_id": str(asset.raster_asset.asset_id),
                    "width_pixels": asset.raster_asset.width_pixels,
                    "height_pixels": asset.raster_asset.height_pixels,
                    "row_stride_bytes": asset.raster_asset.row_stride_bytes,
                    "byte_count": asset.raster_asset.byte_count,
                    "pixel_sha256": asset.raster_asset.pixel_sha256,
                    "pixel_layout": OVERLAY_RASTER_PIXEL_LAYOUT_V1,
                    "color_interpretation": OVERLAY_RASTER_COLOR_INTERPRETATION_V1,
                    "worker_handle": handle,
                },
            }
            for asset, handle in zip(plan.assets, raster_handle_values, strict=True)
        ],
    }
    try:
        payload = json.dumps(
            document,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.MODEL_INVALID,
            "flattened.worker_encode",
        )
    if not 1 <= len(payload) <= MAX_PDF_EXPORT_PLAN_BYTES:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.worker_plan_size",
        )
    return payload


def decode_flattened_pdf_worker_plan(payload: bytes) -> FlattenedPdfWorkerPlan:
    """Decode exact canonical bytes and reconstruct the trusted domain plan."""

    if type(payload) is not bytes or not 1 <= len(payload) <= MAX_PDF_EXPORT_PLAN_BYTES:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.RESULT_INVALID,
            "flattened.worker_plan_size",
        )
    try:
        root = _object(_decode_json(payload), _ROOT_MEMBERS)
        if _integer(root["worker_plan_version"]) != FLATTENED_PDF_WORKER_PLAN_VERSION:
            raise _PlanDecodeError
        if _integer(root["plan_version"]) != FLATTENED_PDF_EXPORT_PLAN_VERSION:
            raise _PlanDecodeError
        base_plan_payload = json.dumps(
            _object_any(root["base_plan"]),
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        decoded_base = decode_pdf_export_worker_plan(base_plan_payload)

        raw_pages = _array(
            root["pages"],
            minimum=1,
            maximum=MAX_PDF_EXPORT_PAGES,
        )
        if len(raw_pages) != len(decoded_base.plan.pages):
            raise _PlanDecodeError
        pages: list[FlattenedPdfExportPage] = []
        for raw_page, base_page in zip(raw_pages, decoded_base.plan.pages, strict=True):
            page = _object(raw_page, _PAGE_MEMBERS)
            if (
                _integer(page["page_version"]) != FLATTENED_PDF_EXPORT_PAGE_VERSION
                or _integer(page["output_page_index"]) != base_page.output_page_index
                or _uuid(page["page_id"]) != base_page.page_id
            ):
                raise _PlanDecodeError
            crop = NormalizedPageBox.from_manifest(_object_any(page["active_crop"]))
            raw_overlays = _array(page["overlays"], minimum=0, maximum=MAX_OVERLAYS)
            overlays = tuple(OverlayRecord.from_manifest(value) for value in raw_overlays)
            pages.append(
                FlattenedPdfExportPage(
                    base_page=base_page,
                    active_crop=crop,
                    overlays=overlays,
                    page_version=_integer(page["page_version"]),
                )
            )

        raw_assets = _array(
            root["assets"],
            minimum=0,
            maximum=MAX_FLATTENED_PDF_EXPORT_RASTER_ASSETS,
        )
        worker_assets: list[FlattenedPdfWorkerAsset] = []
        for raw_asset in raw_assets:
            value = _object(raw_asset, _ASSET_MEMBERS)
            if _integer(value["asset_version"]) != FLATTENED_PDF_EXPORT_ASSET_VERSION:
                raise _PlanDecodeError
            encoded_asset = OverlayAssetRecord.from_manifest(value["encoded_asset"])
            raster_value = _object(value["raster_asset"], _RASTER_MEMBERS)
            if (
                _integer(raster_value["asset_version"]) != OVERLAY_RASTER_ASSET_VERSION
                or _string(raster_value["pixel_layout"]) != OVERLAY_RASTER_PIXEL_LAYOUT_V1
                or _string(raster_value["color_interpretation"])
                != OVERLAY_RASTER_COLOR_INTERPRETATION_V1
            ):
                raise _PlanDecodeError
            raster_asset = OverlayRasterAsset(
                asset_id=_uuid(raster_value["asset_id"]),
                width_pixels=_integer(raster_value["width_pixels"]),
                height_pixels=_integer(raster_value["height_pixels"]),
                row_stride_bytes=_integer(raster_value["row_stride_bytes"]),
                byte_count=_integer(raster_value["byte_count"]),
                pixel_sha256=_string(raster_value["pixel_sha256"]),
                asset_version=_integer(raster_value["asset_version"]),
            )
            worker_assets.append(
                FlattenedPdfWorkerAsset(
                    asset=FlattenedPdfExportAsset(
                        encoded_asset=encoded_asset,
                        raster_asset=raster_asset,
                        asset_version=_integer(value["asset_version"]),
                    ),
                    worker_handle=_integer(raster_value["worker_handle"]),
                )
            )

        plan = FlattenedPdfExportPlan(
            base_plan=decoded_base.plan,
            pages=tuple(pages),
            assets=tuple(item.asset for item in worker_assets),
            plan_version=_integer(root["plan_version"]),
        )
        if plan.semantic_fingerprint != _string(root["semantic_fingerprint"]):
            raise _PlanDecodeError
        decoded = FlattenedPdfWorkerPlan(
            plan=plan,
            sources=decoded_base.sources,
            assets=tuple(worker_assets),
            worker_plan_version=_integer(root["worker_plan_version"]),
        )
        if (
            encode_flattened_pdf_worker_plan(
                decoded.plan,
                {item.source.source_id: item.worker_handle for item in decoded.sources},
                {item.asset.asset_id: item.worker_handle for item in decoded.assets},
            )
            != payload
        ):
            raise _PlanDecodeError
        return decoded
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.RESULT_INVALID,
            "flattened.worker_plan",
        )


def _decode_json(payload: bytes) -> object:
    try:
        document = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_unique_object,
            parse_int=_bounded_integer,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
    except Exception as error:
        raise _PlanDecodeError from error
    _walk(document)
    return document


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise _PlanDecodeError
        result[key] = value
    return result


def _bounded_integer(value: str) -> int:
    parsed = int(value)
    if not -MAX_SAFE_MANIFEST_INTEGER <= parsed <= MAX_SAFE_MANIFEST_INTEGER:
        raise _PlanDecodeError
    return parsed


def _reject_number(_: str) -> Never:
    raise _PlanDecodeError


def _walk(value: object) -> None:
    stack: list[tuple[object, int]] = [(value, 1)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if (
            nodes > MAX_FLATTENED_PDF_WORKER_PLAN_NODES
            or depth > MAX_FLATTENED_PDF_WORKER_PLAN_DEPTH
        ):
            raise _PlanDecodeError
        if type(current) is str:
            if len(current) > MAX_FLATTENED_PDF_WORKER_PLAN_STRING_CODEPOINTS:
                raise _PlanDecodeError
        elif type(current) is list:
            stack.extend((item, depth + 1) for item in current)
        elif type(current) is dict:
            stack.extend((item, depth + 1) for item in cast(dict[str, object], current).values())
        elif current is not None and type(current) not in (int, bool):
            raise _PlanDecodeError


def _object(value: object, members: frozenset[str]) -> dict[str, object]:
    result = _object_any(value)
    if set(result) != members:
        raise _PlanDecodeError
    return result


def _object_any(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise _PlanDecodeError
    return cast(dict[str, object], value)


def _array(value: object, *, minimum: int, maximum: int) -> list[object]:
    if type(value) is not list or not minimum <= len(value) <= maximum:
        raise _PlanDecodeError
    return cast(list[object], value)


def _integer(value: object) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SAFE_MANIFEST_INTEGER:
        raise _PlanDecodeError
    return value


def _string(value: object) -> str:
    if type(value) is not str:
        raise _PlanDecodeError
    return value


def _uuid(value: object) -> UUID:
    text = _string(value)
    try:
        parsed = UUID(text)
    except ValueError as error:
        raise _PlanDecodeError from error
    if parsed.version != 4 or str(parsed) != text:
        raise _PlanDecodeError
    return parsed

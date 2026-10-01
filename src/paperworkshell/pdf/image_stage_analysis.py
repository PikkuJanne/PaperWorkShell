"""Bounded structural image inventory and conservative direct-page placement facts.

Forms, inline images, masks, arbitrary clipping and color substitutions are not
interpreted by this first profile. No stream text or PDF names leave this module.
"""

from __future__ import annotations

import math
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import NoReturn, cast

import pikepdf
from pikepdf._core import StreamParser

from paperworkshell.domain.image_pages import MAX_IMAGE_DIMENSION_PIXELS, MAX_IMAGE_SOURCE_PIXELS
from paperworkshell.domain.pdf_export import PdfExportErrorCode, reject_pdf_export
from paperworkshell.domain.pdf_image_stages import (
    PdfImageAnalysis,
    PdfImageAssetAnalysis,
    PdfImageColorMode,
    PdfImageEligibility,
    PdfImagePageAnalysis,
)
from paperworkshell.pdf.export_adapter import (
    PdfExportAdapterControl,
    PdfExportAdapterLimits,
    _checkpoint,
    _effective_page_attributes,
)


def fail(context: str = "analysis") -> NoReturn:
    reject_pdf_export(PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, f"image_stage.{context}")


def raw_stream(stream: pikepdf.Stream, maximum: int) -> memoryview:
    length: object = stream.get("/Length")
    if type(length) is not int or length < 0:
        fail("length")
    if length > maximum:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "image_stage.stream_bytes")
    if any(key in stream for key in ("/F", "/FFilter", "/FDecodeParms")):
        fail("external_stream")
    raw = memoryview(stream.get_raw_stream_buffer()).cast("B")
    if len(raw) != length:
        fail("length")
    return raw


def filter_name(stream: pikepdf.Stream) -> str | None:
    value = stream.get("/Filter")
    if isinstance(value, pikepdf.Array) and len(value) == 1:
        value = value[0]
    return (
        str(value) if isinstance(value, pikepdf.Name) else None if value is None else "unsupported"
    )


def decoded_plain_stream(
    stream: pikepdf.Stream,
    maximum: int,
    control: PdfExportAdapterControl,
    *,
    maximum_encoded_bytes: int | None = None,
) -> bytes:
    """Decode only parameter-free Flate, with an expansion cap before allocation."""

    raw = raw_stream(stream, maximum if maximum_encoded_bytes is None else maximum_encoded_bytes)
    selected_filter = filter_name(stream)
    if stream.get("/DecodeParms") is not None or selected_filter not in (None, "/FlateDecode"):
        reject_pdf_export(PdfExportErrorCode.SOURCE_UNSUPPORTED, "image_stage.stream_filter")
    if selected_filter is None:
        if len(raw) > maximum:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "image_stage.expansion")
        return bytes(raw)
    result = bytearray()
    decoder = zlib.decompressobj()
    for offset in range(0, len(raw), 65_536):
        block = bytes(raw[offset : offset + 65_536])
        while block:
            _checkpoint(control)
            decoded = decoder.decompress(block, min(65_536, maximum - len(result) + 1))
            if len(result) + len(decoded) > maximum:
                reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "image_stage.expansion")
            result.extend(decoded)
            block = decoder.unconsumed_tail
        if decoder.unused_data:
            fail("flate")
    if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        fail("flate")
    return bytes(result)


@dataclass(slots=True)
class _Asset:
    stream: pikepdf.Stream = field(repr=False)
    pages: set[int] = field(default_factory=set)
    unsupported: bool = False
    placements: list[tuple[float, float]] = field(default_factory=list)
    unknown: bool = False


@dataclass(slots=True)
class _Budget:
    maximum: int
    control: PdfExportAdapterControl
    count: int = 0
    page_ids: set[tuple[int, int]] = field(default_factory=set)
    tree_ids: set[tuple[int, int]] = field(default_factory=set)

    def take(self, amount: int = 1) -> None:
        _checkpoint(self.control)
        self.count += amount
        if self.count > self.maximum:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "image_stage.objects")


def _inventory(
    root: object,
    *,
    page_index: int | None,
    direct: bool,
    assets: dict[tuple[int, int], _Asset],
    budget: _Budget,
) -> None:
    pending = [(root, direct)]
    seen: set[tuple[int, int]] = set()
    while pending:
        value, allowed = pending.pop()
        budget.take()
        if isinstance(value, pikepdf.Stream | pikepdf.Dictionary):
            if isinstance(value, pikepdf.Stream) and value.get("/Subtype") == pikepdf.Name.Image:
                asset = assets.setdefault(value.objgen, _Asset(value))
                if page_index is not None:
                    asset.pages.add(page_index)
                asset.unsupported |= not allowed or page_index is None
            if value.is_indirect:
                if value.objgen in seen:
                    continue
                seen.add(value.objgen)
            if value.objgen in budget.page_ids:
                continue
            keys = sorted(value.keys())
            if value.objgen in budget.tree_ids:
                # Only structural parent links/count/type are excluded. Even
                # inherited resources can contain unused/overridden aliases, so
                # v1 conservatively leaves their images unsupported.
                keys = [
                    key
                    for key in keys
                    if key
                    not in (
                        "/Parent",
                        "/Count",
                        "/Type",
                    )
                ]
            if len(keys) + len(pending) + budget.count > budget.maximum:
                reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "image_stage.objects")
            # Children of any stream/dictionary here are not direct page images.
            pending.extend((value[key], False) for key in reversed(keys))
        elif isinstance(value, pikepdf.Array):
            if len(value) + len(pending) + budget.count > budget.maximum:
                reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "image_stage.objects")
            pending.extend((value[index], False) for index in range(len(value) - 1, -1, -1))


class _PlacementParser(StreamParser):
    """Incremental native callbacks, bounded before accumulating operands/state."""

    def __init__(
        self,
        names: dict[str, _Asset],
        crop: tuple[float, float, float, float],
        user_unit: float,
        budget: _Budget,
    ) -> None:
        super().__init__()
        self.names = names
        self.crop = crop
        self.user_unit = user_unit
        self.budget = budget
        self.matrix = pikepdf.Matrix()
        self.stack: list[pikepdf.Matrix] = []
        self.operands: list[object] = []
        self.operand_count = 0
        self.unknown = False
        self.coverage_unknown = False
        self.coverage = 0

    def handle_object(self, obj: object, offset: int, length: int) -> None:
        self.budget.take()
        if not isinstance(obj, pikepdf.Operator):
            # Native compound operands are already bounded by the admitted content
            # bytes; only cm/Do need retention and at most six operands are useful.
            self.operands.append(obj)
            self.operand_count += 1
            if len(self.operands) > 6:
                self.operands.pop(0)
            return
        operator = str(obj)
        values, self.operands = self.operands, []
        count, self.operand_count = self.operand_count, 0
        if operator == "q":
            if count:
                fail("content_state")
            self.stack.append(self.matrix)
        elif operator == "Q":
            if count or not self.stack:
                fail("content_state")
            self.matrix = self.stack.pop()
        elif operator == "cm":
            if count != 6 or any(type(v) not in (int, float, Decimal) for v in values):
                fail("matrix")
            numbers = tuple(float(cast(float, v)) for v in values)
            if not all(math.isfinite(v) and abs(v) <= 1e12 for v in numbers):
                fail("matrix")
            self.matrix = pikepdf.Matrix(*numbers) @ self.matrix
        elif operator == "Do":
            if count != 1 or not isinstance(values[0], pikepdf.Name):
                fail("placement")
            asset = self.names.get(str(values[0]))
            if asset is None:
                # An executed Form can alter coverage/clipping; its image assets
                # are inventoried separately and never become eligible here.
                self.unknown = True
            else:
                self._place(asset)
        elif operator in ("W", "W*", "gs", "BI", "ID", "EI"):
            self.unknown = True

    def handle_eof(self) -> None:
        if self.stack or self.operands:
            fail("content_state")

    def _place(self, asset: _Asset) -> None:
        a, b, c, d, _e, _f = self.matrix.shorthand
        width, height = math.hypot(a, b), math.hypot(c, d)
        if (
            not all(math.isfinite(v) for v in self.matrix.shorthand)
            or min(width, height, self.user_unit) <= 0
            or abs(a * c + b * d) > width * height * 1e-9
        ):
            asset.unknown = True
            self.unknown = True
            return
        asset.placements.append((width * self.user_unit, height * self.user_unit))
        if not ((b == 0 and c == 0) or (a == 0 and d == 0)):
            # DPI remains known for orthogonal rotation. An axis-aligned box
            # would overstate its footprint, so coverage is deliberately unknown.
            self.coverage_unknown = True
            return
        corners = [self.matrix.transform(point) for point in ((0, 0), (0, 1), (1, 0), (1, 1))]
        left, bottom, right, top = self.crop
        x = max(0.0, min(right, max(p[0] for p in corners)) - max(left, min(p[0] for p in corners)))
        y = max(0.0, min(top, max(p[1] for p in corners)) - max(bottom, min(p[1] for p in corners)))
        self.coverage = max(
            self.coverage,
            min(1_000_000, int(1_000_000 * x * y / ((right - left) * (top - bottom)))),
        )


def _color(stream: pikepdf.Stream) -> PdfImageColorMode:
    value = stream.get("/ColorSpace")
    if value == pikepdf.Name.DeviceRGB:
        return PdfImageColorMode.RGB
    if value == pikepdf.Name.DeviceGray:
        return PdfImageColorMode.GRAYSCALE
    return PdfImageColorMode.UNSUPPORTED


def supported_image(stream: pikepdf.Stream) -> bool:
    bits: object = stream.get("/BitsPerComponent")
    return (
        _color(stream) is not PdfImageColorMode.UNSUPPORTED
        and type(bits) is int
        and bits == 8
        and filter_name(stream) in (None, "/FlateDecode", "/DCTDecode")
        and not any(
            key in stream
            for key in (
                "/DecodeParms",
                "/Decode",
                "/Mask",
                "/SMask",
                "/ImageMask",
                "/SMaskInData",
                "/OC",
                "/Alternates",
                "/F",
                "/FFilter",
                "/FDecodeParms",
                "/Intent",
            )
        )
    )


def analyze_document(
    document: pikepdf.Pdf,
    *,
    baseline_sha256: str,
    baseline_byte_size: int,
    limits: PdfExportAdapterLimits,
    control: PdfExportAdapterControl,
    maximum_memory_bytes: int,
    page_progress: Callable[[int], None] | None = None,
) -> tuple[PdfImageAnalysis, tuple[pikepdf.Stream, ...]]:
    """Inventory page-reachable images; IDs follow stable page/resource traversal."""

    if document.is_encrypted or not 1 <= len(document.pages) <= limits.inspection.maximum_pages:
        fail("document_policy")
    if len(document.objects) > limits.inspection.maximum_objects:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "image_stage.objects")
    assets: dict[tuple[int, int], _Asset] = {}
    budget = _Budget(limits.inspection.maximum_objects, control)
    budget.page_ids = {page.obj.objgen for page in document.pages}
    for page in document.pages:
        current: object = page.obj.get("/Parent")
        seen: set[tuple[int, int]] = set()
        while isinstance(current, pikepdf.Dictionary) and current.objgen not in seen:
            budget.take()
            seen.add(current.objgen)
            budget.tree_ids.add(current.objgen)
            current = current.get("/Parent")
    coverage: list[int | None] = []
    for page_index, page in enumerate(document.pages):
        budget.take()
        attributes = _effective_page_attributes(
            page.obj,
            error_code=PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED,
            context="image_stage.page",
        )
        resources = attributes.resources
        if resources is None:
            resources = pikepdf.Dictionary()
        if not isinstance(resources, pikepdf.Dictionary):
            fail("resources")
        names: dict[str, _Asset] = {}
        xobjects = resources.get("/XObject", pikepdf.Dictionary())
        if not isinstance(xobjects, pikepdf.Dictionary):
            fail("resources")
        for name in sorted(xobjects.keys()):
            stream = xobjects[name]
            _inventory(stream, page_index=page_index, direct=True, assets=assets, budget=budget)
            if isinstance(stream, pikepdf.Stream) and stream.get("/Subtype") == pikepdf.Name.Image:
                names[name] = assets[stream.objgen]
        for name in sorted(resources.keys()):
            if name != "/XObject":
                _inventory(
                    resources[name],
                    page_index=page_index,
                    direct=False,
                    assets=assets,
                    budget=budget,
                )
        for name in sorted(page.obj.keys()):
            if name not in ("/Parent", "/Resources"):
                _inventory(
                    page.obj[name],
                    page_index=page_index,
                    direct=False,
                    assets=assets,
                    budget=budget,
                )
        box = attributes.crop_box
        if not isinstance(box, pikepdf.Array) or len(box) != 4:
            fail("crop")
        crop = tuple(float(value) for value in box)
        if not all(math.isfinite(v) for v in crop) or crop[2] <= crop[0] or crop[3] <= crop[1]:
            fail("crop")
        unit = float(page.obj.get("/UserUnit", 1))
        if not math.isfinite(unit) or not 0 < unit <= 75_000:
            fail("unit")
        parser = _PlacementParser(
            names, cast(tuple[float, float, float, float], crop), unit, budget
        )
        contents = page.obj.get("/Contents")
        streams = (
            list(contents)
            if isinstance(contents, pikepdf.Array)
            else []
            if contents is None
            else [contents]
        )
        decoded_size = 0
        for stream in streams:
            if not isinstance(stream, pikepdf.Stream):
                fail("contents")
            decoded_size += len(
                decoded_plain_stream(stream, maximum_memory_bytes // 8 - decoded_size, control)
            )
        page.parse_contents(parser)
        color_resources = resources.get("/ColorSpace")
        defaults = isinstance(color_resources, pikepdf.Dictionary) and any(
            key in color_resources for key in ("/DefaultRGB", "/DefaultGray")
        )
        for asset in names.values():
            asset.unknown |= parser.unknown
            asset.unsupported |= defaults
        coverage.append(None if parser.unknown or parser.coverage_unknown else parser.coverage)
        if page_progress is not None:
            page_progress(page_index + 1)
    # Non-page aliases (metadata, associated files, etc.) must not silently change
    # through a selected image reference. Page/Pages nodes are traversal barriers.
    _inventory(document.trailer, page_index=None, direct=False, assets=assets, budget=budget)
    records: list[PdfImageAssetAnalysis] = []
    bindings: list[pikepdf.Stream] = []
    for asset in assets.values():
        if not asset.pages:
            continue
        stream = asset.stream
        width: object = stream.get("/Width")
        height: object = stream.get("/Height")
        length: object = stream.get("/Length")
        if any(type(value) is not int or value < 1 for value in (width, height, length)):
            fail("image_dimensions")
        assert isinstance(width, int) and isinstance(height, int) and isinstance(length, int)
        dpi = (
            None
            if asset.unknown or not asset.placements
            else (
                max(1, int(min(72_000 * width / x for x, _ in asset.placements))),
                max(1, int(min(72_000 * height / y for _, y in asset.placements))),
            )
        )
        if len(asset.pages) > 1:
            eligibility = PdfImageEligibility.SHARED
        elif (
            asset.unsupported
            or not supported_image(stream)
            or max(width, height) > MAX_IMAGE_DIMENSION_PIXELS
            or width * height > MAX_IMAGE_SOURCE_PIXELS
        ):
            eligibility = PdfImageEligibility.UNSUPPORTED
        elif dpi is None:
            eligibility = PdfImageEligibility.UNKNOWN_GEOMETRY
        elif min(dpi) < 200_000:
            eligibility = PdfImageEligibility.BELOW_FLOOR
        else:
            eligibility = PdfImageEligibility.ELIGIBLE
        records.append(
            PdfImageAssetAnalysis(
                len(records),
                tuple(sorted(asset.pages)),
                width,
                height,
                None if dpi is None else dpi[0],
                None if dpi is None else dpi[1],
                _color(stream),
                length,
                eligibility,
            )
        )
        bindings.append(stream)
    pages = tuple(
        PdfImagePageAnalysis(
            index,
            tuple(asset.asset_id for asset in records if index in asset.page_indexes),
            sum(asset.encoded_byte_size for asset in records if index in asset.page_indexes),
            coverage[index],
        )
        for index in range(len(document.pages))
    )
    if document.get_warnings():
        fail("syntax_warnings")
    return PdfImageAnalysis(
        baseline_sha256, baseline_byte_size, len(document.pages), tuple(records), pages
    ), tuple(bindings)

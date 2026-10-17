"""Worker-private full rewrite of a derivative using a closed rendering profile.

This removes non-page data; it does not prove that page text or pixels have been
redacted. The fresh file remains private and awaits the separate verifier.
"""

from __future__ import annotations

import logging
import math
import os
import re
import stat
import zlib
from collections.abc import Buffer, Iterator
from contextlib import contextmanager
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import BinaryIO, Never, cast

import pikepdf
from fontTools.ttLib import TTFont, newTable  # type: ignore[import-untyped]

from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode, reject_pdf_export
from paperworkshell.domain.redaction_sanitization import (
    RedactionSanitizationInventory,
    RedactionSanitizationReport,
    RedactionSanitizationRequest,
    validate_sanitization_report,
)
from paperworkshell.pdf.raster_pdf_page import _BoundedOutput
from paperworkshell.pdf.redaction_reconstruction import (
    RedactionReconstructionControl,
    RedactionReconstructionLimits,
    _checkpoint,
    _measure_path,
    _measure_source,
)

# Shared with the structural inspector's accepted graph policy. Every retained
# dictionary has a role: an unknown extension never becomes a generic deep copy.
_DEPTH = 64
_COLLECTION = 65_536
_NODES = 4_000_000
_STRIP = frozenset(
    {
        "/Metadata",
        "/PieceInfo",
        "/LastModified",
        "/StructParent",
        "/StructParents",
        "/AF",
        "/Annots",
        "/A",
        "/AA",
        "/OpenAction",
        "/AcroForm",
        "/Fields",
        "/XFA",
        "/JS",
        "/JavaScript",
        "/EmbeddedFiles",
        "/EF",
        "/V",
        "/DV",
        "/RV",
        "/T",
        "/TU",
        "/TM",
        "/ActualText",
        "/Alt",
        "/Lang",
        "/SpiderInfo",
        "/OPI",
        "/Alternates",
        "/ID",
    }
)
_FIELDS: dict[str, dict[str, str]] = {
    "page": {
        "/MediaBox": "numbers",
        "/CropBox": "numbers",
        "/BleedBox": "numbers",
        "/TrimBox": "numbers",
        "/ArtBox": "numbers",
        "/Rotate": "number",
        "/UserUnit": "number",
        "/Resources": "resources",
        "/Contents": "contents",
        "/Group": "group",
    },
    "resources": {
        "/Font": "font_map",
        "/XObject": "xobject_map",
        "/ExtGState": "gstate_map",
        "/ColorSpace": "colorspace_map",
        "/ProcSet": "names",
    },
    "font": {
        "/Type": "name",
        "/Subtype": "name",
        "/BaseFont": "name",
        "/Name": "name",
        "/FirstChar": "number",
        "/LastChar": "number",
        "/Widths": "numbers",
        "/FontDescriptor": "descriptor",
        "/Encoding": "encoding",
        "/ToUnicode": "cmap",
        "/DescendantFonts": "fonts",
        "/CIDSystemInfo": "cidsystem",
        "/DW": "number",
        "/W": "metric_array",
        "/DW2": "numbers",
        "/W2": "metric_array",
        "/CIDToGIDMap": "name_or_cidmap",
    },
    "descriptor": {
        "/Type": "name",
        "/FontName": "name",
        "/Flags": "number",
        "/FontBBox": "numbers",
        "/ItalicAngle": "number",
        "/Ascent": "number",
        "/Descent": "number",
        "/Leading": "number",
        "/CapHeight": "number",
        "/XHeight": "number",
        "/StemV": "number",
        "/StemH": "number",
        "/AvgWidth": "number",
        "/MaxWidth": "number",
        "/MissingWidth": "number",
        "/FontFile2": "fontprogram",
        "/CIDSet": "cidset",
    },
    "encoding": {"/Type": "name", "/BaseEncoding": "name", "/Differences": "differences"},
    "cidsystem": {"/Registry": "string", "/Ordering": "string", "/Supplement": "number"},
    "group": {"/Type": "name", "/S": "name", "/CS": "colorspace", "/I": "bool", "/K": "bool"},
    "gstate": {
        "/Type": "name",
        "/LW": "number",
        "/LC": "number",
        "/LJ": "number",
        "/ML": "number",
        "/D": "dash",
        "/RI": "name",
        "/OP": "bool",
        "/op": "bool",
        "/OPM": "number",
        "/FL": "number",
        "/SM": "number",
        "/SA": "bool",
        "/BM": "name_or_names",
        "/CA": "number",
        "/ca": "number",
        "/AIS": "bool",
        "/TK": "bool",
    },
    "decode": {
        "/Predictor": "number",
        "/Colors": "number",
        "/BitsPerComponent": "number",
        "/Columns": "number",
        "/EarlyChange": "number",
        "/K": "number",
        "/EndOfLine": "bool",
        "/EncodedByteAlign": "bool",
        "/Rows": "number",
        "/EndOfBlock": "bool",
        "/BlackIs1": "bool",
        "/DamagedRowsBeforeError": "number",
    },
    "content": {},
    "program": {
        "/Length1": "number",
        "/Length2": "number",
        "/Length3": "number",
        "/Subtype": "name",
    },
    "fontprogram": {"/Length1": "number"},
    "cidmap": {},
    "cidset": {},
    "cmap": {
        "/Type": "name",
        "/CMapName": "name",
        "/CIDSystemInfo": "cidsystem",
        "/WMode": "number",
    },
    "icc": {"/N": "number", "/Alternate": "colorspace", "/Range": "numbers"},
    "image": {
        "/Type": "name",
        "/Subtype": "name",
        "/Width": "number",
        "/Height": "number",
        "/ColorSpace": "colorspace",
        "/BitsPerComponent": "number",
        "/Intent": "name",
        "/ImageMask": "bool",
        "/Interpolate": "bool",
        "/Decode": "numbers",
        "/Mask": "image_mask",
        "/SMask": "image",
        "/SMaskInData": "number",
        "/Matte": "numbers",
    },
    "form": {
        "/Type": "name",
        "/Subtype": "name",
        "/FormType": "number",
        "/BBox": "numbers",
        "/Matrix": "numbers",
        "/Resources": "resources",
        "/Group": "group",
    },
}
_STREAM_FIELDS = {"/Filter": "name_or_names", "/DecodeParms": "decode_or_array"}
_FILTERS = frozenset(
    {
        "/FlateDecode",
        "/DCTDecode",
        "/ASCII85Decode",
        "/ASCIIHexDecode",
        "/RunLengthDecode",
        "/LZWDecode",
        "/CCITTFaxDecode",
    }
)
_STANDARD_FONTS = frozenset(
    {
        "/Courier",
        "/Courier-Bold",
        "/Courier-Oblique",
        "/Courier-BoldOblique",
        "/Helvetica",
        "/Helvetica-Bold",
        "/Helvetica-Oblique",
        "/Helvetica-BoldOblique",
        "/Times-Roman",
        "/Times-Bold",
        "/Times-Italic",
        "/Times-BoldItalic",
        "/Symbol",
        "/ZapfDingbats",
    }
)
_NOTO_NAMES = {
    0: "Copyright 2015-2021 Google LLC. All Rights Reserved.",
    1: "Noto Sans",
    2: "Regular",
    3: "2.007;GOOG;NotoSans-Regular",
    4: "Noto Sans Regular",
    5: "Version 2.007",
    6: "NotoSans-Regular",
}
_FONT_TABLES = frozenset(
    {
        "head",
        "hhea",
        "maxp",
        "OS/2",
        "hmtx",
        "cmap",
        "prep",
        "loca",
        "glyf",
        "name",
        "post",
        "gasp",
        "fpgm",
        "cvt ",
    }
)


def _unsupported() -> Never:
    reject_pdf_export(PdfExportErrorCode.SOURCE_UNSUPPORTED, "sanitization.rendering_profile")


@contextmanager
def _private_font_logging_suppressed() -> Iterator[None]:
    """Contain fontTools diagnostics without retaining document-derived values."""
    namespace = logging.getLogger("fontTools")
    selected = {namespace}
    for name, value in tuple(logging.Logger.manager.loggerDict.items()):
        if isinstance(value, logging.Logger) and name.startswith("fontTools."):
            selected.add(value)
    states = tuple(
        (logger, logger.disabled, logger.level, logger.propagate, tuple(logger.handlers))
        for logger in selected
    )
    try:
        for logger in selected:
            logger.disabled = True
        namespace.setLevel(logging.CRITICAL + 1)
        namespace.propagate = False
        namespace.handlers.clear()
        yield
    finally:
        for logger, disabled, level, propagate, handlers in states:
            logger.handlers[:] = handlers
            logger.propagate = propagate
            logger.setLevel(level)
            logger.disabled = disabled


def _canonical_cmap(data: bytes, control: RedactionReconstructionControl) -> bytes:
    """Keep only the pinned OCR renderer's two-byte Unicode mapping grammar."""
    normalized = b" ".join(re.sub(rb"%[^\r\n]*", b"", data).split())
    header = (
        rb"/CIDInit /ProcSet findresource begin 12 dict begin begincmap "
        rb"/CIDSystemInfo <<\s*/Registry \(Adobe\) /Ordering \(UCS\) /Supplement 0 >> def "
        rb"/CMapName /[A-Za-z0-9_-]+ def /CMapType 2 def "
        rb"1 begincodespacerange <0000> <[fF]{4}> endcodespacerange "
    )
    matched = re.fullmatch(
        header + rb"(.*?) endcmap CMapName currentdict /CMap defineresource pop end end", normalized
    )
    if matched is None:
        _unsupported()
    remaining = matched[1]
    mappings: dict[int, bytes] = {}
    while remaining:
        _checkpoint(control)
        block = re.match(rb"([0-9]{1,5}) beginbfchar (.*?) endbfchar(?: |$)", remaining)
        if block is None:
            _unsupported()
        pairs = re.findall(
            rb"<([0-9a-fA-F]{4})> <([0-9a-fA-F]{4}(?:[0-9a-fA-F]{4}){0,31})>", block[2]
        )
        if (
            len(pairs) != int(block[1])
            or b" ".join(b"<" + left + b"> <" + right + b">" for left, right in pairs) != block[2]
        ):
            _unsupported()
        for left, right in pairs:
            _checkpoint(control)
            code = int(left, 16)
            if code in mappings or len(mappings) >= 65536:
                _unsupported()
            # Reject invalid UTF-16 rather than invent extraction behavior.
            try:
                bytes.fromhex(right.decode("ascii")).decode("utf-16-be")
            except UnicodeError, ValueError:
                _unsupported()
            mappings[code] = right.upper()
        remaining = remaining[block.end() :]
    canonical_header = (
        b"/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
        b"/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n"
        b"1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
    )
    lines = [canonical_header]
    ordered = sorted(mappings.items())
    for offset in range(0, len(ordered), 100):
        block_items = ordered[offset : offset + 100]
        lines.append(f"{len(block_items)} beginbfchar\n".encode("ascii"))
        lines.extend(
            f"<{code:04X}> <".encode("ascii") + target + b">\n" for code, target in block_items
        )
        lines.append(b"endbfchar\n")
    lines.append(b"endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n")
    return b"".join(lines)


class _Graph:
    def __init__(
        self, limits: RedactionReconstructionLimits, control: RedactionReconstructionControl
    ) -> None:
        self.limits, self.control = limits, control
        self.nodes = 0
        self.maximum_nodes = min(_NODES, limits.maximum_pdf_objects * 64)

    def visit(self, value: object, depth: int) -> None:
        _checkpoint(self.control)
        self.nodes += 1
        if depth > _DEPTH or self.nodes > self.maximum_nodes:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "sanitization.graph")
        collection = value.stream_dict if isinstance(value, pikepdf.Stream) else value
        if (
            isinstance(collection, (pikepdf.Dictionary, pikepdf.Array))
            and len(collection) > _COLLECTION
        ):
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "sanitization.collection")


def _inventory(
    document: pikepdf.Pdf,
    limits: RedactionReconstructionLimits,
    control: RedactionReconstructionControl,
) -> RedactionSanitizationInventory:
    graph = _Graph(limits, control)
    if not 1 <= len(document.objects) <= limits.maximum_pdf_objects:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "sanitization.objects")
    counts = dict.fromkeys(RedactionSanitizationInventory.__dataclass_fields__, 0)
    # Visit reachable objects with semantic roles before orphan objects.
    # Resource names such as /A are map labels, not action fields.
    roots = iter(document.objects)
    stack: list[tuple[object, int, str]] = [(document.trailer, 0, "dict")]
    seen: set[tuple[int, int]] = set()
    while True:
        if not stack:
            root = next(roots, None)
            if root is None:
                break
            stack.append((root, 0, "dict"))
        current, depth, role = stack.pop()
        if isinstance(current, pikepdf.Object) and current.objgen != (0, 0):
            if current.objgen in seen:
                continue
            seen.add(current.objgen)
        graph.visit(current, depth)
        if isinstance(current, (pikepdf.Dictionary, pikepdf.Stream)):
            value = current.stream_dict if isinstance(current, pikepdf.Stream) else current
            keys = set(map(str, value.keys())) if role != "opaque" else set()
            kind, subtype = (
                (value.get("/Type"), value.get("/Subtype")) if role != "opaque" else (None, None)
            )
            action = value.get("/S") if role != "opaque" else None
            counts["attachments"] += int(
                bool(keys & {"/EmbeddedFiles", "/AF", "/EF"})
                or kind in {pikepdf.Name.EmbeddedFile, pikepdf.Name.Filespec}
                or subtype == pikepdf.Name.FileAttachment
            )
            counts["annotations"] += int(
                "/Annots" in keys
                or kind == pikepdf.Name.Annot
                or subtype
                in {
                    pikepdf.Name.Text,
                    pikepdf.Name.Link,
                    pikepdf.Name.FreeText,
                    pikepdf.Name.Widget,
                }
            )
            counts["actions"] += int(
                bool(keys & {"/A", "/AA", "/OpenAction"})
                or kind == pikepdf.Name.Action
                or action
                in {
                    pikepdf.Name.JavaScript,
                    pikepdf.Name.Launch,
                    pikepdf.Name.URI,
                    pikepdf.Name.GoTo,
                    pikepdf.Name.GoToR,
                    pikepdf.Name.SubmitForm,
                    pikepdf.Name.ImportData,
                }
            )
            counts["scripts"] += int(
                bool(keys & {"/JS", "/JavaScript"}) or action == pikepdf.Name.JavaScript
            )
            counts["form_fields"] += int(
                bool(keys & {"/AcroForm", "/Fields", "/FT", "/XFA", "/V", "/DV", "/RV"})
                or subtype == pikepdf.Name.Widget
            )
            counts["metadata"] += int(
                bool(
                    keys
                    & {
                        "/Info",
                        "/Metadata",
                        "/PieceInfo",
                        "/LastModified",
                        "/ActualText",
                        "/Alt",
                        "/Lang",
                    }
                )
                or kind == pikepdf.Name.Metadata
            )
            counts["signatures"] += int(
                (role != "opaque" and value.get("/FT") == pikepdf.Name.Sig)
                or kind == pikepdf.Name.Sig
                or bool(keys & {"/ByteRange", "/SigFlags", "/DocMDP", "/FieldMDP"})
            )
            counts["incremental_revisions"] += int("/Prev" in keys)
            for key, item in value.items():
                child_role = "resources" if str(key) == "/Resources" else "dict"
                if role == "resources" and str(key) in {
                    "/Font",
                    "/XObject",
                    "/ExtGState",
                    "/ColorSpace",
                    "/Properties",
                    "/Pattern",
                    "/Shading",
                }:
                    child_role = "opaque"
                stack.append((item, depth + 1, child_role))
        elif isinstance(current, pikepdf.Array):
            stack.extend((item, depth + 1, "dict") for item in current)
    return RedactionSanitizationInventory(**counts)


class _Copier:
    def __init__(
        self,
        document: pikepdf.Pdf,
        limits: RedactionReconstructionLimits,
        control: RedactionReconstructionControl,
    ) -> None:
        self.document, self.graph = document, _Graph(limits, control)
        self.stream_bytes = 0
        self.scalar_bytes = 0
        self.materialization_limit = min(
            limits.maximum_memory_bytes // 4, limits.maximum_output_bytes
        )
        self.memo: dict[tuple[tuple[int, int], str], pikepdf.Object] = {}
        self.active: set[tuple[tuple[int, int], str]] = set()

    def copy(self, value: object, role: str, depth: int = 0) -> object:
        self.graph.visit(value, depth)
        if role == "number":
            if type(value) not in (int, float, Decimal) or not math.isfinite(
                float(cast(float, value))
            ):
                _unsupported()
            return value
        if role == "bool":
            if type(value) is not bool:
                _unsupported()
            return value
        if role in {"name", "string"}:
            if not isinstance(value, pikepdf.Name if role == "name" else pikepdf.String):
                _unsupported()
            scalar_data = str(value) if role == "name" else bytes(value)
            self.scalar_bytes += len(scalar_data)
            if self.scalar_bytes + self.stream_bytes > self.materialization_limit:
                reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "sanitization.materialization")
            return pikepdf.Name(str(value)) if role == "name" else pikepdf.String(bytes(value))
        if role == "encoding" and isinstance(value, pikepdf.Name):
            if str(value) not in {
                "/Identity-H",
                "/Identity-V",
                "/WinAnsiEncoding",
                "/MacRomanEncoding",
                "/MacExpertEncoding",
                "/StandardEncoding",
            }:
                _unsupported()
            return self.copy(value, "name", depth + 1)
        if role == "xobject":
            if not isinstance(value, pikepdf.Stream):
                _unsupported()
            subtype = value.get("/Subtype")
            if subtype not in {pikepdf.Name.Image, pikepdf.Name.Form}:
                _unsupported()
            role = "image" if subtype == pikepdf.Name.Image else "form"
        if role.startswith("name_or_"):
            if (
                role == "name_or_cidmap"
                and isinstance(value, pikepdf.Name)
                and value != pikepdf.Name.Identity
            ):
                _unsupported()
            return self.copy(
                value, "name" if isinstance(value, pikepdf.Name) else role[8:], depth + 1
            )
        if role == "image_mask":
            return self.copy(
                value, "numbers" if isinstance(value, pikepdf.Array) else "image", depth + 1
            )
        if role == "colorspace":
            return self._colorspace(value, depth)
        if role == "contents" and isinstance(value, pikepdf.Stream):
            return self.copy(value, "content", depth + 1)
        if role in {
            "numbers",
            "names",
            "fonts",
            "contents",
            "differences",
            "metric_array",
            "dash",
            "decode_or_array",
        } and isinstance(value, pikepdf.Array):
            children: list[object] = []
            for item in cast(list[object], value):
                if role == "numbers":
                    child = "number"
                elif role == "names":
                    child = "name"
                elif role == "fonts":
                    child = "font"
                elif role == "contents":
                    child = "content"
                elif role == "differences":
                    child = "name" if isinstance(item, pikepdf.Name) else "number"
                elif role in {"metric_array", "dash"}:
                    child = "numbers" if isinstance(item, pikepdf.Array) else "number"
                else:
                    if item is None:
                        children.append(None)
                        continue
                    child = "decode"
                children.append(self.copy(item, child, depth + 1))
            return pikepdf.Array(children)
        if role == "decode_or_array":
            role = "decode"
        if role.endswith("_map"):
            if not isinstance(value, pikepdf.Dictionary):
                _unsupported()
            return pikepdf.Dictionary(
                {
                    str(self.copy(pikepdf.Name(str(key)), "name", depth + 1)): self.copy(
                        item, role[:-4], depth + 1
                    )
                    for key, item in value.items()
                }
            )
        if role in {"numbers", "names", "fonts", "contents", "differences", "metric_array", "dash"}:
            _unsupported()
        if role not in _FIELDS or not isinstance(value, (pikepdf.Dictionary, pikepdf.Stream)):
            _unsupported()
        stream = isinstance(value, pikepdf.Stream)
        if stream != (
            role
            in {
                "content",
                "program",
                "fontprogram",
                "cidmap",
                "cidset",
                "cmap",
                "icc",
                "image",
                "form",
            }
        ):
            _unsupported()
        identity = (value.objgen, role)
        if value.objgen != (0, 0):
            if identity in self.active:
                _unsupported()
            if identity in self.memo:
                return self.memo[identity]
            self.active.add(identity)
        if role == "font" and value.get("/Subtype") not in {
            pikepdf.Name.Type0,
            pikepdf.Name.Type1,
            pikepdf.Name.MMType1,
            pikepdf.Name.TrueType,
            pikepdf.Name.CIDFontType0,
            pikepdf.Name.CIDFontType2,
        }:
            _unsupported()
        if role == "group" and value.get("/S") != pikepdf.Name.Transparency:
            _unsupported()
        if role == "icc" or role == "program":
            _unsupported()
        if role == "cidsystem" and (
            bytes(value.get("/Registry", pikepdf.String(""))) != b"Adobe"
            or bytes(value.get("/Ordering", pikepdf.String(""))) != b"UCS"
            or value.get("/Supplement") != 0
        ):
            _unsupported()
        fields = _FIELDS[role] | (_STREAM_FIELDS if stream else {})
        data: bytes | None = None
        if isinstance(value, pikepdf.Stream):
            raw = self._raw(value)
            if role == "image":
                data = self._image(value, raw)
            elif role in {"cmap", "fontprogram", "cidmap", "cidset"}:
                decoded = self._decoded(value, raw)
                if role == "cmap":
                    data = _canonical_cmap(decoded, self.graph.control)
                elif role == "fontprogram":
                    data = self._font(decoded)
                elif role == "cidmap":
                    if len(decoded) != 131072:
                        _unsupported()
                    data = decoded
                else:
                    if len(decoded) > 8192:
                        _unsupported()
                    data = decoded
            else:
                if role in {"content", "form"}:
                    self._decoded(value, raw)
                data = raw
            self.stream_bytes += max(0, len(data) - len(raw))
            if self.stream_bytes + self.scalar_bytes > self.materialization_limit:
                reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "sanitization.materialization")
        target = (
            pikepdf.Stream(self.document, data)
            if isinstance(value, pikepdf.Stream)
            else pikepdf.Dictionary()
        )
        for key, item in value.items():
            name = str(key)
            if name in _STRIP or stream and name == "/Length":
                continue
            if role in {"image", "cmap", "fontprogram", "cidmap", "cidset"} and name in {
                "/Filter",
                "/DecodeParms",
                "/Length1",
            }:
                continue
            if name not in fields:
                _unsupported()
            if role == "cmap" and name == "/CMapName":
                target[name] = pikepdf.Name("/Adobe-Identity-UCS")
                continue
            if role in {"font", "descriptor"} and name in {"/BaseFont", "/FontName", "/Name"}:
                font_name = str(item)
                if font_name in _STANDARD_FONTS:
                    target[name] = pikepdf.Name(font_name)
                elif re.fullmatch(r"/(?:[A-Z]{6}\+)?NotoSans", font_name):
                    target[name] = pikepdf.Name("/NotoSans")
                else:
                    _unsupported()
                continue
            target[name] = self.copy(item, fields[name], depth + 1)
        if role == "fontprogram":
            target["/Length1"] = len(cast(bytes, data))
        if stream:
            filters = value.get("/Filter")
            for filter_name in (
                filters
                if isinstance(filters, pikepdf.Array)
                else (() if filters is None else (filters,))
            ):
                if str(filter_name) not in _FILTERS:
                    _unsupported()
        if value.objgen != (0, 0):
            if not stream:
                target = self.document.make_indirect(target)
            self.memo[identity] = target
            self.active.remove(identity)
        return target

    def _decoded(self, value: pikepdf.Stream, raw: bytes, maximum: int | None = None) -> bytes:
        maximum = min(
            self.materialization_limit,
            maximum if maximum is not None else self.materialization_limit,
        )
        if value.get("/DecodeParms") is not None:
            _unsupported()
        filter_name = value.get("/Filter")
        if filter_name is None:
            if len(raw) > maximum:
                reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "sanitization.decoded_bytes")
            return raw
        if filter_name != pikepdf.Name.FlateDecode:
            _unsupported()
        decoder = zlib.decompressobj()
        blocks: list[bytes] = []
        count = 0
        for offset in range(0, len(raw), 64 * 1024):
            _checkpoint(self.graph.control)
            block = decoder.decompress(raw[offset : offset + 64 * 1024], maximum - count + 1)
            count += len(block)
            if count > maximum or decoder.unconsumed_tail:
                reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "sanitization.decoded_bytes")
            blocks.append(block)
        if not decoder.eof or decoder.unused_data:
            _unsupported()
        return b"".join(blocks)

    def _image(self, value: pikepdf.Stream, raw: bytes) -> bytes:
        width: object = value.get("/Width")
        height: object = value.get("/Height")
        channels = 3 if value.get("/ColorSpace") == pikepdf.Name.DeviceRGB else 1
        if type(width) is not int or type(height) is not int or width < 1 or height < 1:
            _unsupported()
        if (
            not isinstance(value.get("/ColorSpace"), pikepdf.Name)
            or value.get("/ColorSpace") not in {pikepdf.Name.DeviceRGB, pikepdf.Name.DeviceGray}
            or value.get("/BitsPerComponent") != 8
            or any(
                key in value
                for key in {"/Mask", "/SMask", "/SMaskInData", "/ImageMask", "/Decode", "/Matte"}
            )
        ):
            _unsupported()
        if (
            width > self.graph.limits.maximum_width_pixels
            or height > self.graph.limits.maximum_height_pixels
            or width * height > self.graph.limits.maximum_pixels
        ):
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "sanitization.image_dimensions")
        expected = width * height * channels
        decoded = self._decoded(value, raw, min(expected, self.graph.limits.maximum_raster_bytes))
        if len(decoded) != expected:
            _unsupported()
        return decoded

    def _font(self, decoded: bytes) -> bytes:
        with _private_font_logging_suppressed():
            return self._normalize_font(decoded)

    def _normalize_font(self, decoded: bytes) -> bytes:
        with TTFont(BytesIO(decoded), recalcTimestamp=False, lazy=False) as font:
            if set(font.keys()) - {"GlyphOrder"} - _FONT_TABLES or not {
                "head",
                "hhea",
                "maxp",
                "OS/2",
                "hmtx",
                "cmap",
                "loca",
                "glyf",
                "name",
                "post",
            } <= set(font.keys()):
                _unsupported()
            if any(
                font["name"].getDebugName(name_id) != text for name_id, text in _NOTO_NAMES.items()
            ):
                _unsupported()
            if not 1 <= font["maxp"].numGlyphs <= 4096:
                _unsupported()
            # Fixed public font identity retains Google's approved attribution.
            names = newTable("name")
            names.names = []
            for name_id, text in _NOTO_NAMES.items():
                names.setName(text, name_id, 3, 1, 0x409)
            font["name"] = names
            font["head"].created = font["head"].modified = 2082844800
            font["OS/2"].achVendID = "GOOG"
            font["post"].formatType = 3.0
            for table in font.keys():
                _checkpoint(self.graph.control)
                if table != "GlyphOrder":
                    font[table]  # Parse every retained table before serialization.
            output = BytesIO()
            font.save(
                cast(BinaryIO, _Output(output, self.materialization_limit, self.graph.control))
            )
            return output.getvalue()

    def _raw(self, value: pikepdf.Stream) -> bytes:
        size: object = value.get("/Length")
        if type(size) is not int or size < 0:
            _unsupported()
        self.stream_bytes += size
        if self.stream_bytes + self.scalar_bytes > self.materialization_limit:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "sanitization.stream_bytes")
        data = value.read_raw_bytes()
        if len(data) != size:
            _unsupported()
        return data

    def _colorspace(self, value: object, depth: int) -> object:
        if isinstance(value, pikepdf.Name):
            # Named resources resolve in the same freshly copied resource map.
            return self.copy(value, "name", depth + 1)
        if not isinstance(value, pikepdf.Array) or len(value) not in {2, 4}:
            _unsupported()
        kind = value[0]
        if kind == pikepdf.Name.ICCBased and len(value) == 2:
            return pikepdf.Array([kind, self.copy(value[1], "icc", depth + 1)])
        if kind == pikepdf.Name.Indexed and len(value) == 4:
            return pikepdf.Array(
                [
                    kind,
                    self.copy(value[1], "colorspace", depth + 1),
                    self.copy(value[2], "number", depth + 1),
                    self.copy(
                        value[3],
                        "string" if isinstance(value[3], pikepdf.String) else "program",
                        depth + 1,
                    ),
                ]
            )
        _unsupported()


class _Output(_BoundedOutput):
    def __init__(
        self, stream: BinaryIO, maximum: int, control: RedactionReconstructionControl
    ) -> None:
        super().__init__(stream, maximum)
        self.control = control

    def write(self, data: Buffer, /) -> int:
        _checkpoint(self.control)
        return super().write(data)


def _paths(output: Path, scratch: Path) -> None:
    if (
        not isinstance(output, Path)
        or not isinstance(scratch, Path)
        or not output.is_absolute()
        or not scratch.is_absolute()
    ):
        reject_pdf_export(PdfExportErrorCode.DESTINATION_INVALID, "sanitization.paths")
    try:
        for directory in (output.parent, scratch):
            info = directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise OSError
        if output.exists() or output.is_symlink():
            reject_pdf_export(PdfExportErrorCode.DESTINATION_EXISTS, "sanitization.exists")
    except PdfExportError:
        raise
    except OSError:
        reject_pdf_export(PdfExportErrorCode.DESTINATION_INVALID, "sanitization.paths")


class PikepdfRedactionSanitizer:
    """Create an unsigned private derivative with no non-page payloads."""

    def sanitize(
        self,
        source: BinaryIO,
        output_path: Path,
        scratch_directory: Path,
        request: RedactionSanitizationRequest,
        limits: RedactionReconstructionLimits,
        control: RedactionReconstructionControl,
    ) -> RedactionSanitizationReport:
        _paths(output_path, scratch_directory)
        if (
            type(request) is not RedactionSanitizationRequest
            or type(limits) is not RedactionReconstructionLimits
        ):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "sanitization.request")
        request.__post_init__()
        limits.__post_init__()
        initial = _measure_source(source, limits.maximum_source_bytes, control)
        if initial != (request.source_byte_size, request.source_sha256):
            reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "sanitization.source")
        if (
            request.source_byte_size + limits.maximum_output_bytes
            > limits.maximum_temporary_storage_bytes
        ):
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "sanitization.temporary_bytes")
        original = source.tell()
        owned, completed = False, False
        bounded: _Output | None = None
        try:
            source.seek(0)
            with (
                pikepdf.Pdf.open(
                    source,
                    attempt_recovery=False,
                    suppress_warnings=True,
                    inherit_page_attributes=True,
                ) as document,
                pikepdf.Pdf.new() as target,
            ):
                if document.is_encrypted:
                    reject_pdf_export(PdfExportErrorCode.SOURCE_ENCRYPTED, "sanitization.encrypted")
                if (
                    len(document.pages) != request.source_page_count
                    or request.source_page_count > limits.maximum_pages
                ):
                    reject_pdf_export(PdfExportErrorCode.SOURCE_INCONSISTENT, "sanitization.pages")
                before = _inventory(document, limits, control)
                if document.get_warnings():
                    reject_pdf_export(PdfExportErrorCode.SOURCE_INCONSISTENT, "sanitization.syntax")
                copier = _Copier(target, limits, control)
                for page in document.pages:
                    _checkpoint(control)
                    if "/OC" in page.obj:
                        _unsupported()
                    result = target.add_blank_page().obj
                    del result["/Contents"]
                    for key, role in _FIELDS["page"].items():
                        if key in page.obj:
                            result[key] = copier.copy(page.obj[key], role)
                    if "/MediaBox" not in result:
                        _unsupported()
                with output_path.open("xb") as raw:
                    owned = True
                    bounded = _Output(raw, limits.maximum_output_bytes, control)
                    target.save(
                        cast(BinaryIO, bounded),
                        deterministic_id=True,
                        encryption=False,
                        compress_streams=True,
                        object_stream_mode=pikepdf.ObjectStreamMode.disable,
                        normalize_content=False,
                        linearize=False,
                    )
                    bounded.flush()
                    os.fsync(raw.fileno())
            with pikepdf.Pdf.open(
                output_path, attempt_recovery=False, suppress_warnings=True
            ) as reopened:
                after = _inventory(reopened, limits, control)
                if (
                    not after.empty
                    or reopened.is_encrypted
                    or len(reopened.pages) != request.source_page_count
                    or set(map(str, reopened.Root.keys())) != {"/Type", "/Pages"}
                    or "/Info" in reopened.trailer
                    or "/Prev" in reopened.trailer
                    or reopened.check_pdf_syntax(progress=lambda _value: _checkpoint(control))
                ):
                    reject_pdf_export(
                        PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "sanitization.reopen"
                    )
            size, digest = _measure_path(output_path, limits.maximum_output_bytes, control)
            if _measure_source(source, limits.maximum_source_bytes, control) != initial:
                reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "sanitization.source_recheck")
            report = RedactionSanitizationReport(
                request.fingerprint,
                request.source_sha256,
                digest,
                size,
                request.source_page_count,
                before,
                after,
            )
            validate_sanitization_report(request, report)
            _checkpoint(control)
            completed = True
            return report
        except PdfExportError:
            raise
        except MemoryError, RecursionError:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "sanitization.resources")
        except Exception:
            if bounded is not None and bounded.limit_exceeded:
                reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "sanitization.output_bytes")
            reject_pdf_export(PdfExportErrorCode.ASSEMBLY_FAILED, "sanitization.failed")
        finally:
            try:
                if source.seek(original) != original:
                    reject_pdf_export(
                        PdfExportErrorCode.SOURCE_CHANGED, "sanitization.source_restore"
                    )
            except Exception:
                completed = False
                reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "sanitization.source_restore")
            finally:
                if owned and not completed:
                    try:
                        output_path.unlink(missing_ok=True)
                    except OSError:
                        reject_pdf_export(
                            PdfExportErrorCode.ASSEMBLY_FAILED, "sanitization.cleanup"
                        )

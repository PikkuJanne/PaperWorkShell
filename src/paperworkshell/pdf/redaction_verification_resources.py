"""Bind every page resource to the authenticated reconstructed reference.

Unused resources are included. Rendered image enumeration alone cannot prove
their absence. This comparison is independent of the sanitizer implementation.
"""

from __future__ import annotations

import hashlib
import re
import struct
from decimal import ROUND_DOWN, Decimal, localcontext
from typing import BinaryIO

import pikepdf

from paperworkshell.pdf.redaction_reconstruction import (
    RedactionReconstructionControl,
    RedactionReconstructionLimits,
)
from paperworkshell.pdf.redaction_verification_structure import (
    StructuralEvidence,
    _Inspection,
    _Lex,
    _Rejected,
)

_ENCODING = frozenset({"/Length", "/Filter", "/DecodeParms", "/Length1"})
_RESOURCE_MAPS = frozenset({"/Font", "/XObject", "/ExtGState", "/ColorSpace"})
_REMOVED = frozenset(
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


def _font_identity(data: bytes, inspection: _Inspection, *, candidate: bool) -> bytes:
    """Compare all SFNT tables, normalizing only documented head bookkeeping.

    No name, glyph, metric, hint, or other binary table data is ignored. Fonts
    requiring other metadata normalization fail closed in this initial profile.
    """
    if len(data) < 12 or data[:4] != b"\x00\x01\x00\x00":
        raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
    count = struct.unpack_from(">H", data, 4)[0]
    if not 1 <= count <= 32 or 12 + count * 16 > len(data):
        raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
    power = 1 << (count.bit_length() - 1)
    if struct.unpack_from(">HHH", data, 6) != (
        power * 16,
        power.bit_length() - 1,
        count * 16 - power * 16,
    ):
        raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
    records: dict[bytes, tuple[int, int]] = {}
    directory: dict[bytes, int] = {}
    for at in range(12, 12 + count * 16, 16):
        inspection.checkpoint()
        tag, checksum, offset, length = struct.unpack_from(">4sIII", data, at)
        if tag in records or offset % 4 or offset < 12 + count * 16 or offset + length > len(data):
            raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
        records[tag] = (offset, length)
        directory[tag] = at
        payload = data[offset : offset + length]
        if tag == b"head":
            if length != 54:
                raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
            payload = payload[:8] + b"\0" * 4 + payload[12:]
        if _checksum(payload, inspection) != checksum:
            raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
    end = 12 + count * 16
    for offset, length in sorted(records.values()):
        if offset < end or offset - end > 3 or any(data[end:offset]):
            raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
        end = offset + length
    if len(data) - end > 3 or any(data[end:]):
        raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
    if _checksum(data, inspection) != 0xB1B0AFBA or b"head" not in records:
        raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
    normalized = bytearray(data)
    for tag, (offset, length) in sorted(records.items()):
        inspection.checkpoint()
        payload = data[offset : offset + length]
        if tag == b"head":
            if (
                length != 54
                or candidate
                and payload[20:36] != struct.pack(">QQ", 2082844800, 2082844800)
            ):
                raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
            normalized[offset + 8 : offset + 12] = b"\0" * 4
            normalized[offset + 20 : offset + 36] = b"\0" * 16
            at = directory[tag]
            normalized[at + 4 : at + 8] = b"\0" * 4
    return hashlib.sha256(normalized).digest()


def _checksum(data: bytes, inspection: _Inspection) -> int:
    total = 0
    for offset in range(0, len(data), 4):
        if offset % (64 * 1024) == 0:
            inspection.checkpoint()
        total = (
            total + int.from_bytes(data[offset : offset + 4].ljust(4, b"\0"), "big")
        ) & 0xFFFFFFFF
    return total


def _cmap_identity(data: bytes, inspection: _Inspection, *, candidate: bool) -> bytes:
    """Compare the closed Unicode map grammar independently of mapping order."""
    lexer = _Lex(data, inspection)
    header = _Lex(
        b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def /CMapName /Adobe-Identity-UCS def /CMapType 2 def 1 begincodespacerange <0000> <FFFF> endcodespacerange",
        inspection,
    )
    previous = b""
    while (expected := header.token())[0] != "eof":
        actual = lexer.token()
        if previous == b"/CMapName" and actual[0] == "name":
            if candidate and actual != expected:
                raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
        elif actual != expected:
            raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
        previous = expected[1]
    mappings: dict[bytes, bytes] = {}
    while True:
        token = lexer.token()
        if token == ("word", b"endcmap"):
            break
        if (
            token[0] != "number"
            or re.fullmatch(rb"[0-9]{1,5}", token[1]) is None
            or lexer.token() != ("word", b"beginbfchar")
        ):
            raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
        count = int(token[1])
        if len(mappings) + count > min(
            65536,
            inspection.limits.maximum_pdf_objects,
            inspection.limits.maximum_memory_bytes // 1024,
        ):
            raise _Rejected("RV_LIMIT_EXCEEDED")
        for _ in range(count):
            left, right = lexer.token(), lexer.token()
            if (
                left[0] != "string"
                or right[0] != "string"
                or len(left[1]) != 2
                or not 2 <= len(right[1]) <= 64
                or len(right[1]) % 2
                or left[1] in mappings
            ):
                raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
            right[1].decode("utf-16-be", errors="strict")
            mappings[left[1]] = right[1]
        if lexer.token() != ("word", b"endbfchar"):
            raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
    footer = _Lex(b"CMapName currentdict /CMap defineresource pop end end", inspection)
    while (expected := footer.token())[0] != "eof":
        if lexer.token() != expected:
            raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
    if lexer.token()[0] != "eof":
        raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
    digest = hashlib.sha256()
    for key, value in sorted(mappings.items()):
        inspection.checkpoint()
        digest.update(key + struct.pack(">I", len(value)) + value)
    return digest.digest()


class _Resources:
    def __init__(self, inspection: _Inspection, *, candidate: bool) -> None:
        self.inspection = inspection
        self.candidate = candidate
        self.memo: dict[tuple[tuple[int, int], str, bool], bytes] = {}
        self.active: set[tuple[int, int]] = set()

    def identity(
        self, value: object, role: str = "", depth: int = 0, opaque_map: bool = False
    ) -> bytes:
        self.inspection.node(depth)
        indirect = value.objgen if isinstance(value, pikepdf.Object) else (0, 0)
        key = (indirect, role, opaque_map)
        if indirect != (0, 0):
            if key in self.memo:
                return self.memo[key]
            if indirect in self.active:
                raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
            self.active.add(indirect)
        digest = hashlib.sha256()
        if isinstance(value, (pikepdf.Dictionary, pikepdf.Stream)):
            dictionary = value.stream_dict if isinstance(value, pikepdf.Stream) else value
            if len(dictionary) > self.inspection.limits.maximum_pdf_objects:
                raise _Rejected("RV_LIMIT_EXCEEDED")
            digest.update(b"stream" if isinstance(value, pikepdf.Stream) else b"dictionary")
            for name, child in sorted(dictionary.items(), key=lambda item: str(item[0])):
                text = str(name)
                if not opaque_map and text in _REMOVED:
                    if self.candidate:
                        raise _Rejected("RV_FEATURE_RETAINED")
                    continue
                if isinstance(value, pikepdf.Stream) and text in _ENCODING:
                    continue
                encoded_key = text.encode("utf-8")
                digest.update(struct.pack(">I", len(encoded_key)))
                digest.update(encoded_key)
                digest.update(
                    self.identity(
                        child,
                        "" if opaque_map else text,
                        depth + 1,
                        not opaque_map and text in _RESOURCE_MAPS,
                    )
                )
            if isinstance(value, pikepdf.Stream):
                decoded = self.inspection.decoded(value)
                length1: object = dictionary.get("/Length1")
                if (
                    self.candidate
                    and length1 is not None
                    and (
                        role != "/FontFile2" or type(length1) is not int or length1 != len(decoded)
                    )
                ):
                    raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
                if role == "/FontFile2":
                    digest.update(
                        _font_identity(decoded, self.inspection, candidate=self.candidate)
                    )
                elif role == "/ToUnicode":
                    digest.update(
                        _cmap_identity(decoded, self.inspection, candidate=self.candidate)
                    )
                elif role == "/Contents" or value.get("/Subtype") == pikepdf.Name.Form:
                    lexer = _Lex(decoded, self.inspection)
                    while True:
                        kind, token = lexer.token()
                        if kind == "eof":
                            break
                        if kind == "number":
                            number = Decimal(token.decode("ascii"))
                            token = ("0" if number == 0 else str(number.normalize())).encode(
                                "ascii"
                            )
                        digest.update(kind.encode())
                        digest.update(struct.pack(">I", len(token)))
                        digest.update(token)
                else:
                    digest.update(hashlib.sha256(decoded).digest())
        elif isinstance(value, pikepdf.Array):
            if len(value) > self.inspection.limits.maximum_pdf_objects:
                raise _Rejected("RV_LIMIT_EXCEEDED")
            digest.update(b"array")
            for child in value:
                digest.update(self.identity(child, role, depth + 1))
        elif isinstance(value, pikepdf.String):
            digest.update(b"string" + bytes(value))
        elif isinstance(value, pikepdf.Name):
            name = str(value)
            if role in {"/BaseFont", "/FontName", "/Name"} and re.fullmatch(
                r"/(?:[A-Z]{6}\+)?NotoSans", name
            ):
                if self.candidate and name != "/NotoSans":
                    raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
                name = "/NotoSans"
            if role == "/CMapName":
                if self.candidate and name != "/Adobe-Identity-UCS":
                    raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
                name = "/Adobe-Identity-UCS"
            digest.update(b"name" + name.encode("utf-8"))
        elif value is None or type(value) is bool:
            digest.update(str(value).encode())
        elif type(value) in {int, float, Decimal}:
            number = Decimal(str(value))
            if not number.is_finite():
                raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
            digest.update(b"number" + ("0" if number == 0 else str(number.normalize())).encode())
        else:
            raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
        result = digest.digest()
        if indirect != (0, 0):
            self.active.remove(indirect)
            self.memo[key] = result
        return result


def _canonical_page_tree(
    document: pikepdf.Pdf, inspection: _Inspection, *, viewer_resave: bool = False
) -> None:
    catalog_fields = {"/Type", "/Pages"}
    if viewer_resave and "/Metadata" in document.Root:
        catalog_fields.add("/Metadata")
    if (
        set(map(str, document.Root.keys())) != catalog_fields
        or document.Root.Type != pikepdf.Name.Catalog
    ):
        raise _Rejected("RV_FEATURE_RETAINED")
    stack: list[tuple[object, int, tuple[int, int] | None]] = [(document.Root.Pages, 0, None)]
    seen: set[tuple[int, int]] = set()
    nodes: list[pikepdf.Dictionary] = []
    page_fields = {
        "/Type",
        "/Parent",
        "/MediaBox",
        "/CropBox",
        "/BleedBox",
        "/TrimBox",
        "/ArtBox",
        "/Rotate",
        "/UserUnit",
        "/Resources",
        "/Contents",
        "/Group",
    }
    while stack:
        node, depth, parent = stack.pop()
        inspection.node(depth)
        if not isinstance(node, pikepdf.Dictionary) or node.objgen == (0, 0) or node.objgen in seen:
            raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
        seen.add(node.objgen)
        nodes.append(node)
        keys = set(map(str, node.keys()))
        if parent is None and "/Parent" in keys:
            raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
        if parent is not None and ("/Parent" not in node or node.Parent.objgen != parent):
            raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
        if node.get("/Type") == pikepdf.Name.Pages:
            if (
                keys - {"/Type", "/Count", "/Kids", "/Parent"}
                or not {"/Type", "/Count", "/Kids"} <= keys
            ):
                raise _Rejected("RV_FEATURE_RETAINED")
            children = node.Kids
            if (
                not isinstance(children, pikepdf.Array)
                or len(children) + len(stack) > inspection.limits.maximum_pdf_objects
            ):
                raise _Rejected("RV_LIMIT_EXCEEDED")
            stack.extend((child, depth + 1, node.objgen) for child in children)
        elif node.get("/Type") != pikepdf.Name.Page or keys - page_fields:
            raise _Rejected("RV_FEATURE_RETAINED")
    counts: dict[tuple[int, int], int] = {}
    for node in reversed(nodes):
        inspection.checkpoint()
        if node.Type == pikepdf.Name.Page:
            counts[node.objgen] = 1
        else:
            total = sum(counts[child.objgen] for child in node.Kids)
            actual_count: object = node.Count
            if type(actual_count) is not int or actual_count != total:
                raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
            counts[node.objgen] = total


def _viewer_coordinate(actual: object, reference: object) -> bool:
    """Accept only the observed 13-significant-digit truncation of page bounds.

    Adobe serializes the two fractional MediaBox coordinates with ten/eleven
    decimal places.  This is not a general tolerance: the exact truncation of
    the authenticated value must match, and the difference is below 1e-10 pt.
    Content operators, glyphs and every resource number remain exact.
    """
    if type(actual) not in {int, Decimal} or type(reference) not in {int, Decimal}:
        return False
    left, right = Decimal(str(actual)), Decimal(str(reference))
    if not left.is_finite() or not right.is_finite():
        return False
    if left == right:
        return True
    if not Decimal(10) <= abs(right) < Decimal(1000):
        return False
    quantum = Decimal(1).scaleb(right.adjusted() - 12)
    with localcontext() as context:
        context.prec = max(32, len(right.as_tuple().digits) + 4)
        truncated = right.quantize(quantum, rounding=ROUND_DOWN)
    return (
        left == truncated
        and len(left.normalize().as_tuple().digits) <= 13
        and abs(left - right) < Decimal("1e-10")
    )


def _page_identity(
    page: pikepdf.Page,
    resources: _Resources,
    *,
    viewer_resave: bool = False,
    reference: pikepdf.Page | None = None,
) -> bytes:
    # Parent edges are authenticated separately and would introduce a cycle.
    fields: dict[str, object] = {
        str(key): child for key, child in page.obj.items() if str(key) != "/Parent"
    }
    if viewer_resave:
        if type(fields.get("/Rotate")) is int and fields["/Rotate"] == 0:
            del fields["/Rotate"]
        media = fields.get("/MediaBox")
        crop = fields.get("/CropBox")
        if isinstance(media, pikepdf.Array) and crop == media:
            del fields["/CropBox"]
        if reference is not None:
            trusted = reference.obj.get("/MediaBox")
            if (
                not isinstance(media, pikepdf.Array)
                or not isinstance(trusted, pikepdf.Array)
                or len(media) != 4
                or len(trusted) != 4
                or not all(_viewer_coordinate(a, b) for a, b in zip(media, trusted, strict=True))
            ):
                raise _Rejected("RV_ORIGINAL_OBJECT_RETAINED")
            # A new dictionary binds the equivalent numeric geometry without
            # modifying the authenticated viewer document or its original bytes.
            fields["/MediaBox"] = pikepdf.Object.parse(trusted.unparse())
    value = pikepdf.Dictionary(fields)
    return resources.identity(value)


def verify_resource_identity(
    candidate: BinaryIO,
    reference: BinaryIO,
    limits: RedactionReconstructionLimits,
    control: RedactionReconstructionControl,
    *,
    viewer_resave: bool = False,
) -> StructuralEvidence:
    """Compare complete resource trees, including unused binary payloads."""
    positions: list[tuple[BinaryIO, int]] = []
    result = StructuralEvidence(False, "RV_METHOD_FAILED")
    try:
        if type(limits) is not RedactionReconstructionLimits or type(viewer_resave) is not bool:
            raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
        limits.__post_init__()
        for stream in (candidate, reference):
            positions.append((stream, stream.tell()))
            stream.seek(0, 2)
            if not 1 <= stream.tell() <= limits.maximum_source_bytes:
                raise _Rejected("RV_LIMIT_EXCEEDED")
            stream.seek(0)
        with (
            pikepdf.Pdf.open(
                candidate,
                attempt_recovery=False,
                suppress_warnings=True,
                inherit_page_attributes=False,
            ) as actual,
            pikepdf.Pdf.open(
                reference,
                attempt_recovery=False,
                suppress_warnings=True,
                inherit_page_attributes=False,
            ) as trusted,
        ):
            if (
                len(actual.objects) + len(trusted.objects) > limits.maximum_pdf_objects
                or not 1 <= len(actual.pages) == len(trusted.pages) <= limits.maximum_pages
            ):
                raise _Rejected("RV_LIMIT_EXCEEDED")
            left = _Resources(_Inspection((), (), limits, control), candidate=True)
            right = _Resources(_Inspection((), (), limits, control), candidate=False)
            if viewer_resave:
                from paperworkshell.pdf.redaction_verification_viewer_metadata import (
                    validate_viewer_metadata,
                )

                validate_viewer_metadata(actual, left.inspection)
            _canonical_page_tree(actual, left.inspection, viewer_resave=viewer_resave)
            for index in range(len(actual.pages)):
                left.inspection.checkpoint()
                if _page_identity(
                    actual.pages[index],
                    left,
                    viewer_resave=viewer_resave,
                    reference=trusted.pages[index] if viewer_resave else None,
                ) != _page_identity(trusted.pages[index], right, viewer_resave=viewer_resave):
                    raise _Rejected("RV_ORIGINAL_OBJECT_RETAINED")
            if actual.get_warnings() or trusted.get_warnings():
                raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
            result = StructuralEvidence(True, "RV_OBJECTS_CLEAR")
    except _Rejected as error:
        result = StructuralEvidence(False, error.code)
    except MemoryError, RecursionError:
        result = StructuralEvidence(False, "RV_LIMIT_EXCEEDED")
    except Exception:
        result = StructuralEvidence(False, "RV_METHOD_FAILED")
    finally:
        for stream, position in positions:
            try:
                if stream.seek(position) != position:
                    raise OSError
            except Exception:
                result = StructuralEvidence(False, "RV_METHOD_FAILED")
    return result

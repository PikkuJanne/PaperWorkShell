"""Independent, bounded inspection of two distinct redaction evidence profiles.

This adapter is for a restricted worker or the synthetic development suite. It
does not authorize publication. The canonical profile remains a fresh,
non-linearized classic-xref PDF, as emitted by the final sanitizer. The explicit
viewer-resave profile also permits a fully proved two-section linearized
envelope and narrowly validated inert bookkeeping. Neither profile trusts a
sanitizer inventory or sanitizer success flag; unknown structures fail closed.
"""

from __future__ import annotations

import hashlib
import re
import zlib
from dataclasses import dataclass
from typing import BinaryIO

import pikepdf

from paperworkshell.pdf.redaction_reconstruction import (
    RedactionReconstructionControl,
    RedactionReconstructionLimits,
)

_CHUNK = 64 * 1024
_SPACE = b" \t\r\n\x00\x0c"
_DELIMITERS = _SPACE + b"()<>[]{}/%"
_PROHIBITED_KEYS = frozenset(
    "/AF /EF /EmbeddedFiles /Annots /AcroForm /XFA /FT /V /DV /RV /A /AA /OpenAction "
    "/JS /JavaScript /Info /Metadata /PieceInfo /LastModified /ActualText /Alt /Lang "
    "/ByteRange /SigFlags /DocMDP /FieldMDP /OC /OCProperties /Thumb /Alternates /OPI "
    "/RichMediaContent /RichMediaSettings /Collection /Perms /SpiderInfo /Outlines /Names".split()
)
_PROHIBITED_TYPES = frozenset(
    "/EmbeddedFile /Filespec /Annot /Action /Metadata /Sig /OCG /OCMD /ObjStm /XRef".split()
)
_ALLOWED_KEYS = frozenset(
    "/Type /Subtype /Pages /Count /Kids /Parent /MediaBox /CropBox /BleedBox /TrimBox /ArtBox "
    "/Rotate /UserUnit /Resources /Contents /Group /Font /XObject /ExtGState /ColorSpace /ProcSet "
    "/BaseFont /Name /FirstChar /LastChar /Widths /FontDescriptor /Encoding /ToUnicode "
    "/DescendantFonts /CIDSystemInfo /DW /W /DW2 /W2 /CIDToGIDMap /FontName /Flags /FontBBox "
    "/ItalicAngle /Ascent /Descent /Leading /CapHeight /XHeight /StemV /StemH /AvgWidth "
    "/MaxWidth /MissingWidth /FontFile2 /CIDSet /BaseEncoding /Differences /Registry /Ordering "
    "/Supplement /S /CS /I /K /LW /LC /LJ /ML /D /RI /OP /op /OPM /FL /SM /SA /BM /CA /ca "
    "/AIS /TK /Length /Filter /Length1 /Length2 /Length3 /CMapName /WMode /N /Alternate /Range "
    "/Width /Height /BitsPerComponent /Intent /ImageMask /Interpolate /Decode /Mask /SMask "
    "/SMaskInData /Matte /FormType /BBox /Matrix /Root /Size /ID".split()
)
_RESOURCE_MAPS = frozenset({"/Font", "/XObject", "/ExtGState", "/ColorSpace"})


@dataclass(frozen=True, slots=True)
class StructuralEvidence:
    passed: bool
    code: str
    object_count: int = 0
    decoded_bytes: int = 0


class _Rejected(Exception):
    def __init__(self, code: str) -> None:
        self.code = code


class _Inspection:
    def __init__(
        self,
        tokens: tuple[str, ...],
        hashes: tuple[str, ...],
        limits: RedactionReconstructionLimits,
        control: RedactionReconstructionControl,
    ) -> None:
        self.limits, self.control = limits, control
        self.materialization = min(limits.maximum_memory_bytes // 8, limits.maximum_output_bytes)
        self.patterns = tuple(
            dict.fromkeys(
                value.encode(encoding)
                for value in tokens
                for encoding in ("utf-8", "utf-16-be", "utf-16-le")
            )
        )
        self.hashes = frozenset(hashes)
        self.nodes = self.decoded_bytes = self.object_count = 0

    def checkpoint(self) -> None:
        if self.control.cancellation_requested():
            raise _Rejected("RV_CANCELLED")
        if self.control.deadline_expired():
            raise _Rejected("RV_TIMED_OUT")

    def scan(self, data: bytes) -> None:
        self.checkpoint()
        for pattern in self.patterns:
            self.checkpoint()
            if pattern in data:
                raise _Rejected("RV_CANARY_RECOVERED")

    def node(self, depth: int) -> None:
        self.checkpoint()
        self.nodes += 1
        if depth > 64 or self.nodes > self.limits.maximum_pdf_objects * 64:
            raise _Rejected("RV_LIMIT_EXCEEDED")

    def decoded(self, stream: pikepdf.Stream) -> bytes:
        self.checkpoint()
        length: object = stream.get("/Length")
        if type(length) is not int or not 0 <= length <= self.materialization:
            raise _Rejected("RV_LIMIT_EXCEEDED")
        if "/DecodeParms" in stream:
            raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
        raw = stream.read_raw_bytes()
        if len(raw) != length:
            raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
        self.scan(raw)
        filter_name = stream.get("/Filter")
        if filter_name is None:
            output = raw
        elif filter_name == pikepdf.Name.FlateDecode:
            if not raw:
                output = b""
            else:
                decoder = zlib.decompressobj()
                blocks: list[bytes] = []
                total = 0
                for offset in range(0, len(raw), _CHUNK):
                    self.checkpoint()
                    block = decoder.decompress(
                        raw[offset : offset + _CHUNK], self.materialization - total + 1
                    )
                    total += len(block)
                    if total > self.materialization or decoder.unconsumed_tail:
                        raise _Rejected("RV_LIMIT_EXCEEDED")
                    blocks.append(block)
                if not decoder.eof or decoder.unused_data:
                    raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
                output = b"".join(blocks)
        else:
            raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
        self.decoded_bytes += len(output)
        if self.decoded_bytes > self.limits.maximum_temporary_storage_bytes:
            raise _Rejected("RV_LIMIT_EXCEEDED")
        self.scan(output)
        if hashlib.sha256(output).hexdigest() in self.hashes:
            raise _Rejected("RV_ORIGINAL_OBJECT_RETAINED")
        return output


class _Lex:
    """Small lexical reader; values are parsed without decoding native streams."""

    def __init__(self, data: bytes, inspection: _Inspection) -> None:
        self.data, self.inspection, self.position = data, inspection, 0
        self.pending: list[tuple[str, bytes]] = []

    def token(self) -> tuple[str, bytes]:
        self.inspection.node(0)
        if self.pending:
            return self.pending.pop()
        data = self.data
        while self.position < len(data) and data[self.position] in _SPACE:
            self.position += 1
        start = self.position
        if start == len(data):
            return "eof", b""
        byte = data[start]
        self.position += 1
        if byte == 37:
            raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
        if byte == 40:
            output, nesting = bytearray(), 1
            while self.position < len(data):
                value = data[self.position]
                self.position += 1
                if value == 92:
                    if self.position >= len(data):
                        break
                    value = data[self.position]
                    self.position += 1
                    escapes = {110: 10, 114: 13, 116: 9, 98: 8, 102: 12}
                    if value in escapes:
                        output.append(escapes[value])
                    elif 48 <= value <= 55:
                        digits = bytes([value])
                        for _ in range(2):
                            if self.position < len(data) and 48 <= data[self.position] <= 55:
                                digits += data[self.position : self.position + 1]
                                self.position += 1
                        output.append(int(digits, 8) & 255)
                    elif value in {10, 13}:
                        if value == 13 and data[self.position : self.position + 1] == b"\n":
                            self.position += 1
                    else:
                        output.append(value)
                elif value == 40:
                    nesting += 1
                    if nesting > 64:
                        raise _Rejected("RV_LIMIT_EXCEEDED")
                    output.append(value)
                elif value == 41:
                    nesting -= 1
                    if not nesting:
                        result = bytes(output)
                        self.inspection.scan(result)
                        return "string", result
                    output.append(value)
                else:
                    output.append(value)
            raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
        if byte in {60, 62}:
            if data[self.position : self.position + 1] == bytes([byte]):
                self.position += 1
                return "delimiter", data[start : self.position]
            if byte == 62:
                raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
            close = data.find(b">", self.position)
            if close < 0:
                raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
            encoded = b"".join(data[self.position : close].split())
            if len(encoded) % 2:
                encoded += b"0"
            result = bytes.fromhex(encoded.decode("ascii"))
            self.position = close + 1
            self.inspection.scan(result)
            return "string", result
        if byte in {91, 93}:
            return "delimiter", bytes([byte])
        if byte == 47:
            while self.position < len(data) and data[self.position] not in _DELIMITERS:
                self.position += 1
            result = re.sub(
                rb"#([0-9a-fA-F]{2})",
                lambda match: bytes([int(match[1], 16)]),
                data[start : self.position],
            )
            self.inspection.scan(result)
            return "name", result
        while self.position < len(data) and data[self.position] not in _DELIMITERS:
            self.position += 1
        if self.position == start:
            raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
        result = data[start : self.position]
        return (
            "number" if re.fullmatch(rb"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)", result) else "word"
        ), result

    def value(self, first: tuple[str, bytes] | None = None, depth: int = 0) -> None:
        self.inspection.node(depth)
        kind, value = self.token() if first is None else first
        if value == b"<<" and kind == "delimiter":
            keys: set[bytes] = set()
            while True:
                key = self.token()
                if key == ("delimiter", b">>"):
                    return
                if key[0] != "name" or key[1] in keys:
                    raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
                keys.add(key[1])
                self.value(depth=depth + 1)
        elif value == b"[" and kind == "delimiter":
            while True:
                item = self.token()
                if item == ("delimiter", b"]"):
                    return
                self.value(item, depth + 1)
        elif kind == "number":
            next_item = self.token()
            if next_item[0] == "number":
                third = self.token()
                if third == ("word", b"R"):
                    return
                self.pending.extend((third, next_item))
                return
            self.pending.append(next_item)
        elif kind not in {"name", "string"} and (kind, value) not in {
            ("word", b"null"),
            ("word", b"true"),
            ("word", b"false"),
        }:
            raise _Rejected("RV_STRUCTURE_UNSUPPORTED")


def _raw_envelope(source: BinaryIO, inspection: _Inspection) -> tuple[int, int]:
    source.seek(0, 2)
    size = source.tell()
    if (
        not 1
        <= size
        <= min(inspection.limits.maximum_source_bytes, inspection.limits.maximum_output_bytes)
    ):
        raise _Rejected("RV_LIMIT_EXCEEDED")
    source.seek(0)
    header = source.read(64)
    if not re.match(rb"%PDF-[12]\.[0-9]\r?\n", header):
        raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
    source.seek(max(0, size - 128))
    final = source.read(128)
    ending = re.search(rb"startxref\s+([0-9]+)\s+%%EOF[\x00\t\n\x0c\r ]*\Z", final)
    if ending is None:
        raise _Rejected("RV_REVISION_RETAINED")
    xref = int(ending[1])
    if not 0 < xref < size:
        raise _Rejected("RV_REVISION_RETAINED")
    source.seek(0)
    overlap = max([len(value) for value in inspection.patterns] + [16]) - 1
    carried = b""
    eof_count = start_count = 0
    consumed = 0
    while block := source.read(_CHUNK):
        consumed += len(block)
        if consumed > size:
            raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
        inspection.scan(carried + block)
        combined = carried + block
        boundary = len(carried)
        eof_count += sum(match.end() > boundary for match in re.finditer(rb"%%EOF", combined))
        start_count += sum(match.end() > boundary for match in re.finditer(rb"startxref", combined))
        carried = combined[-overlap:]
    if consumed != size:
        raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
    if eof_count != 1 or start_count != 1:
        raise _Rejected("RV_REVISION_RETAINED")
    return size, xref


def _physical_objects(
    source: BinaryIO, document: pikepdf.Pdf, xref: int, inspection: _Inspection
) -> None:
    entries = document.get_xref_table()
    if any(entry.type != 1 or type(entry.offset) is not int for entry in entries.values()):
        raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
    positions = sorted(
        (int(entry.offset), identity)
        for identity, entry in entries.items()
        if entry.offset is not None
    )
    if not positions or positions[0][0] > inspection.materialization:
        raise _Rejected("RV_LIMIT_EXCEEDED")
    source.seek(0)
    initial = source.read(positions[0][0])
    if (
        re.fullmatch(rb"%PDF-[12]\.[0-9]\r?\n%\xbf\xf7\xa2\xfe\r?\n[\x00\t\n\x0c\r ]*", initial)
        is None
    ):
        raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
    for index, (offset, identity) in enumerate(positions):
        inspection.checkpoint()
        end = positions[index + 1][0] if index + 1 < len(positions) else xref
        if not 0 < offset < end or end - offset > inspection.materialization:
            raise _Rejected("RV_LIMIT_EXCEEDED")
        source.seek(offset)
        data = source.read(end - offset)
        lexer = _Lex(data, inspection)
        if [lexer.token() for _ in range(3)] != [
            ("number", str(identity[0]).encode()),
            ("number", str(identity[1]).encode()),
            ("word", b"obj"),
        ]:
            raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
        lexer.value()
        next_item = lexer.token()
        if next_item == ("word", b"stream"):
            item = document.get_object(identity)
            length: object = item.get("/Length") if isinstance(item, pikepdf.Stream) else None
            if not isinstance(item, pikepdf.Stream) or type(length) is not int:
                raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
            eol = re.match(rb"\r\n|\n|\r", data[lexer.position :])
            if eol is None:
                raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
            lexer.position += len(eol[0]) + length
            if lexer.position > len(data) or lexer.token() != ("word", b"endstream"):
                raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
            next_item = lexer.token()
        if next_item != ("word", b"endobj") or lexer.token() != ("eof", b""):
            raise _Rejected("RV_STRUCTURE_UNSUPPORTED")

    source.seek(xref)
    tail = source.read(inspection.materialization + 1)
    if len(tail) > inspection.materialization:
        raise _Rejected("RV_LIMIT_EXCEEDED")
    match = re.fullmatch(
        rb"xref\s+(.*?)\s+trailer\s+(.*?)\s+startxref\s+([0-9]+)\s+%%EOF\s*", tail, re.S
    )
    if match is None or int(match[3]) != xref:
        raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
    lines = match[1].splitlines()
    at, recorded = 0, {}
    while at < len(lines):
        section = re.fullmatch(rb"([0-9]+)\s+([0-9]+)", lines[at].strip())
        if section is None:
            raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
        first, count = int(section[1]), int(section[2])
        at += 1
        if count > inspection.limits.maximum_pdf_objects + 1 or at + count > len(lines):
            raise _Rejected("RV_LIMIT_EXCEEDED")
        for number in range(first, first + count):
            record = re.fullmatch(rb"([0-9]{10}) ([0-9]{5}) ([nf])\s*", lines[at])
            at += 1
            if record is None:
                raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
            offset, generation = int(record[1]), int(record[2])
            if record[3] == b"n":
                key = (number, generation)
                if key in recorded:
                    raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
                recorded[key] = offset
            elif number != 0 or offset != 0 or generation != 65535:
                raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
    if recorded != {key: value.offset for key, value in entries.items()}:
        raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
    lexer = _Lex(match[2], inspection)
    lexer.value()
    if lexer.token() != ("eof", b""):
        raise _Rejected("RV_STRUCTURE_UNSUPPORTED")


def _objects(
    document: pikepdf.Pdf,
    inspection: _Inspection,
    *,
    auxiliary: frozenset[tuple[int, int]] = frozenset(),
    info_objgen: tuple[int, int] | None = None,
    metadata_objgen: tuple[int, int] | None = None,
    viewer_resave: bool = False,
    linearized: bool = False,
) -> None:
    seen: set[tuple[int, int]] = set(auxiliary)
    contexts: set[tuple[tuple[int, int], bool, str]] = set()
    stack: list[tuple[object, int, bool, str, str]] = [(document.trailer, 0, False, "", "")]
    while True:
        if not stack:
            if any(item.objgen not in seen for item in document.objects):
                raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
            return
        current, depth, opaque_map, role, parent_role = stack.pop()
        inspection.node(depth)
        if isinstance(current, pikepdf.Object) and current.objgen != (0, 0):
            if current.objgen in auxiliary:
                # Physical envelope auxiliaries must remain unreachable. Inert
                # metadata is accepted only at its exact authenticated edge;
                # its bytes cannot be reused as a page/content/resource object.
                permitted_auxiliary = viewer_resave and (
                    parent_role == "trailer"
                    and role == "/Info"
                    and current.objgen == info_objgen
                    or parent_role == "catalog"
                    and role == "/Metadata"
                    and current.objgen == metadata_objgen
                )
                if not permitted_auxiliary:
                    raise _Rejected("RV_ORIGINAL_OBJECT_RETAINED")
                continue
            context = (
                current.objgen,
                opaque_map,
                role if role in {"/Contents", "/ToUnicode"} else "",
            )
            if context in contexts:
                continue
            contexts.add(context)
            seen.add(current.objgen)
        if isinstance(current, (pikepdf.Dictionary, pikepdf.Stream)):
            dictionary = current.stream_dict if isinstance(current, pikepdf.Stream) else current
            if len(dictionary) + len(stack) > inspection.limits.maximum_pdf_objects:
                raise _Rejected("RV_LIMIT_EXCEEDED")
            keys = set(map(str, current.keys()))
            trailer = depth == 0
            catalog = current.objgen != (0, 0) and current.objgen == document.Root.objgen
            permitted: set[str] = set()
            if viewer_resave and trailer:
                permitted.add("/Info")
                if linearized:
                    permitted.add("/Prev")
            if viewer_resave and catalog:
                permitted.add("/Metadata")
            if "/Prev" in keys - permitted or "/XRefStm" in keys:
                raise _Rejected("RV_REVISION_RETAINED")
            if not opaque_map:
                if (keys - permitted) & _PROHIBITED_KEYS:
                    raise _Rejected("RV_FEATURE_RETAINED")
                if keys - _ALLOWED_KEYS - permitted:
                    raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
                if str(current.get("/Type", "")) in _PROHIBITED_TYPES:
                    raise _Rejected("RV_FEATURE_RETAINED")
                if str(current.get("/Subtype", "")) in {
                    "/Text",
                    "/Link",
                    "/FreeText",
                    "/Widget",
                    "/FileAttachment",
                }:
                    raise _Rejected("RV_FEATURE_RETAINED")
            if isinstance(current, pikepdf.Stream):
                decoded = inspection.decoded(current)
                content_stream = role == "/Contents" or current.get("/Subtype") == pikepdf.Name.Form
                if content_stream or role == "/ToUnicode":
                    lexer = _Lex(decoded, inspection)
                    while (token := lexer.token())[0] != "eof":
                        if content_stream and (
                            token
                            in {
                                ("word", b"BI"),
                                ("word", b"ID"),
                                ("word", b"EI"),
                                ("word", b"BDC"),
                                ("word", b"BMC"),
                                ("word", b"EMC"),
                                ("word", b"DP"),
                                ("word", b"MP"),
                            }
                            or token[0] == "name"
                            and token[1]
                            in {b"/ActualText", b"/Alt", b"/Lang", b"/OC", b"/Metadata"}
                        ):
                            raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
            for key, value in current.items():
                inspection.scan(str(key).encode("utf-8"))
                stack.append(
                    (
                        value,
                        depth + 1,
                        not opaque_map and str(key) in _RESOURCE_MAPS,
                        str(key),
                        "trailer" if trailer else "catalog" if catalog else "dictionary",
                    )
                )
        elif isinstance(current, pikepdf.Array):
            if len(current) + len(stack) > inspection.limits.maximum_pdf_objects:
                raise _Rejected("RV_LIMIT_EXCEEDED")
            stack.extend((value, depth + 1, False, role, "array") for value in current)
        elif isinstance(current, pikepdf.String):
            inspection.scan(bytes(current))
            inspection.scan(str(current).encode("utf-8"))
        elif isinstance(current, pikepdf.Name):
            inspection.scan(str(current).encode("utf-8"))


def _scan_identifier_pair(identifiers: pikepdf.Array, inspection: _Inspection) -> None:
    """Inspect the closed pair in raw and independently decoded string views.

    The caller already requires exactly two 16-byte strings. Native string
    conversion decodes each PDFDocEncoding/UTF-16 value separately, so a second
    BOM cannot conceal a removed value split across the two identifiers.
    """
    views = [
        tuple(dict.fromkeys((bytes(value), str(value).encode("utf-8")))) for value in identifiers
    ]

    def scan(data: bytes) -> None:
        inspection.scan(data)
        if hashlib.sha256(data).hexdigest() in inspection.hashes:
            raise _Rejected("RV_ORIGINAL_OBJECT_RETAINED")

    for group in views:
        for value in group:
            scan(value)
    for first, second in ((views[0], views[1]), (views[1], views[0])):
        for left in first:
            for right in second:
                scan(left + right)


def inspect_structure(
    candidate: BinaryIO,
    *,
    forbidden_tokens: tuple[str, ...],
    forbidden_stream_sha256: tuple[str, ...],
    limits: RedactionReconstructionLimits,
    control: RedactionReconstructionControl,
    viewer_resave: bool = False,
) -> StructuralEvidence:
    """Inspect every physical/indexed object and carrier; preserve input position.

    Supplied source digests must describe affected-page content/image streams,
    rather than shared fonts or unaffected pages. Equality deliberately rejects
    even harmless reuse; object-number changes do not establish absence.
    """
    inspection: _Inspection | None = None
    original: int | None = None
    result = StructuralEvidence(False, "RV_METHOD_FAILED")
    try:
        original = candidate.tell()
        if (
            type(limits) is not RedactionReconstructionLimits
            or type(viewer_resave) is not bool
            or type(forbidden_tokens) is not tuple
            or type(forbidden_stream_sha256) is not tuple
            or len(forbidden_tokens) + len(forbidden_stream_sha256) > limits.maximum_pdf_objects
            or any(
                type(value) is not str or not 1 <= len(value) <= 1024 for value in forbidden_tokens
            )
            or any(
                type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None
                for value in forbidden_stream_sha256
            )
            or sum(len(value.encode("utf-8")) * 5 for value in forbidden_tokens)
            > limits.maximum_memory_bytes // 16
        ):
            raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
        inspection = _Inspection(forbidden_tokens, forbidden_stream_sha256, limits, control)
        if viewer_resave:
            # Bound parsing before the native reader opens the original bytes.
            candidate.seek(0, 2)
            size = candidate.tell()
            if not 1 <= size <= min(limits.maximum_source_bytes, limits.maximum_output_bytes):
                raise _Rejected("RV_LIMIT_EXCEEDED")
        else:
            _, xref = _raw_envelope(candidate, inspection)
        candidate.seek(0)
        with pikepdf.Pdf.open(
            candidate,
            attempt_recovery=False,
            suppress_warnings=True,
            inherit_page_attributes=False,
        ) as document:
            if document.is_encrypted:
                raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
            auxiliary: frozenset[tuple[int, int]] = frozenset()
            info_objgen = metadata_objgen = None
            linearized = viewer_resave and document.is_linearized
            if viewer_resave:
                from paperworkshell.pdf.redaction_verification_viewer_metadata import (
                    validate_viewer_metadata,
                )

                metadata = validate_viewer_metadata(document, inspection)
                auxiliary = metadata.auxiliary_objgens
                info_objgen, metadata_objgen = metadata.info_objgen, metadata.metadata_objgen
                if linearized:
                    from paperworkshell.pdf.redaction_verification_linearized import (
                        inspect_linearized_envelope,
                    )

                    auxiliary |= inspect_linearized_envelope(candidate, document, inspection)
                else:
                    _, xref = _raw_envelope(candidate, inspection)
            trailer_keys = set(map(str, document.trailer.keys()))
            allowed_trailer = {"/Root", "/Size", "/ID"}
            if viewer_resave:
                allowed_trailer.add("/Info")
                if linearized:
                    allowed_trailer.add("/Prev")
            trailer_size: object = document.trailer.get("/Size")
            identifiers = document.trailer.get("/ID")
            if (
                trailer_keys - allowed_trailer
                or not {"/Root", "/Size"} <= trailer_keys
                or type(trailer_size) is not int
                or trailer_size != len(document.objects) + 1
                or identifiers is not None
                and (
                    not isinstance(identifiers, pikepdf.Array)
                    or len(identifiers) != 2
                    or any(
                        not isinstance(value, pikepdf.String) or len(bytes(value)) != 16
                        for value in identifiers
                    )
                )
            ):
                raise _Rejected("RV_FEATURE_RETAINED")
            if isinstance(identifiers, pikepdf.Array):
                _scan_identifier_pair(identifiers, inspection)
            inspection.object_count = len(document.objects)
            if not 1 <= inspection.object_count <= limits.maximum_pdf_objects:
                raise _Rejected("RV_LIMIT_EXCEEDED")
            if not 1 <= len(document.pages) <= limits.maximum_pages:
                raise _Rejected("RV_LIMIT_EXCEEDED")
            if not linearized:
                _physical_objects(candidate, document, xref, inspection)
            _objects(
                document,
                inspection,
                auxiliary=auxiliary,
                info_objgen=info_objgen,
                metadata_objgen=metadata_objgen,
                viewer_resave=viewer_resave,
                linearized=linearized,
            )
            if document.get_warnings() or document.check_pdf_syntax(
                progress=lambda _value: inspection.checkpoint()
            ):
                raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
        inspection.checkpoint()
        result = StructuralEvidence(
            True, "RV_OBJECTS_CLEAR", inspection.object_count, inspection.decoded_bytes
        )
    except _Rejected as error:
        result = StructuralEvidence(
            False,
            error.code,
            inspection.object_count if inspection else 0,
            inspection.decoded_bytes if inspection else 0,
        )
    except MemoryError, RecursionError:
        result = StructuralEvidence(False, "RV_LIMIT_EXCEEDED")
    except Exception:
        result = StructuralEvidence(False, "RV_METHOD_FAILED")
    finally:
        if original is not None:
            try:
                candidate.seek(original)
                if candidate.tell() != original:
                    result = StructuralEvidence(False, "RV_METHOD_FAILED")
            except Exception:
                result = StructuralEvidence(False, "RV_METHOD_FAILED")
    return result

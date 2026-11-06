"""Closed physical proof for the viewer-resave profile's linearized envelope.

This reads the original bytes, not a qpdf rewrite. Two classic cross-reference
sections are accepted only when they partition one complete physical object set.
The forward ``/Prev`` is a linearization link, never a revision-history waiver.
Hint tables are bounded, completely parsed numeric structures; they are not
opaque exceptions to object inspection. Optimization advice is not publication
authority and does not replace the other verification methods.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from dataclasses import dataclass
from typing import BinaryIO, Never, cast

import pikepdf

from paperworkshell.pdf.redaction_verification_structure import _Inspection, _Lex, _Rejected

_SPACE = b" \t\r\n\x00\x0c"
_EOL = rb"(?:\r\n|\r|\n)"
_CHUNK = 64 * 1024
_Identity = tuple[int, int]


def _reject() -> Never:
    raise _Rejected("RV_STRUCTURE_UNSUPPORTED")


def _integer(lexer: _Lex) -> int:
    token = lexer.token()
    if token[0] != "number" or re.fullmatch(rb"[0-9]+", token[1]) is None:
        _reject()
    return int(token[1])


def _reference(lexer: _Lex) -> _Identity:
    result = (_integer(lexer), _integer(lexer))
    if result[0] < 1 or result[1] != 0 or lexer.token() != ("word", b"R"):
        _reject()
    return result


def _read(source: BinaryIO, start: int, end: int, inspection: _Inspection) -> bytes:
    inspection.checkpoint()
    if start < 0 or not start < end or end - start > inspection.materialization:
        raise _Rejected("RV_LIMIT_EXCEEDED")
    source.seek(start)
    result = source.read(end - start)
    if len(result) != end - start:
        _reject()
    return result


@dataclass(frozen=True, slots=True)
class _Trailer:
    size: int
    root: _Identity | None
    info: _Identity | None
    previous: int | None
    identifiers: tuple[bytes, bytes] | None


def _trailer(data: bytes, inspection: _Inspection) -> _Trailer:
    lexer = _Lex(data, inspection)
    if lexer.token() != ("delimiter", b"<<"):
        _reject()
    values: dict[bytes, object] = {}
    while (token := lexer.token()) != ("delimiter", b">>"):
        if token[0] != "name" or token[1] in values:
            _reject()
        key = token[1]
        if key in {b"/Size", b"/Prev"}:
            values[key] = _integer(lexer)
        elif key in {b"/Root", b"/Info"}:
            values[key] = _reference(lexer)
        elif key == b"/ID":
            if lexer.token() != ("delimiter", b"["):
                _reject()
            pair = (lexer.token(), lexer.token())
            if any(kind != "string" or len(value) != 16 for kind, value in pair):
                _reject()
            if lexer.token() != ("delimiter", b"]"):
                _reject()
            values[key] = (pair[0][1], pair[1][1])
        else:
            _reject()
    if lexer.token() != ("eof", b"") or b"/Size" not in values:
        _reject()
    # Values have been read with their exact closed field types above.
    return _Trailer(
        cast(int, values[b"/Size"]),
        cast(_Identity | None, values.get(b"/Root")),
        cast(_Identity | None, values.get(b"/Info")),
        cast(int | None, values.get(b"/Prev")),
        cast(tuple[bytes, bytes] | None, values.get(b"/ID")),
    )


def _xref(
    source: BinaryIO, start: int, end: int, target: int, inspection: _Inspection
) -> tuple[dict[_Identity, int], _Trailer, int]:
    data = _read(source, start, end, inspection)
    match = re.fullmatch(
        rb"xref" + _EOL + rb"(.*?)trailer[\x00\t\n\x0c\r ]*(<<.*?>>)"
        rb"[\x00\t\n\x0c\r ]*startxref[\x00\t\n\x0c\r ]+([0-9]+)"
        rb"[\x00\t\n\x0c\r ]+%%EOF[\x00\t\n\x0c\r ]*",
        data,
        re.S,
    )
    if match is None or int(match[3]) != target:
        _reject()
    entries: dict[_Identity, int] = {}
    numbers: set[int] = set()
    position = 0
    zero_record = -1
    body = match[1]
    while position < len(body):
        inspection.checkpoint()
        section = re.match(rb"([0-9]+) ([0-9]+)" + _EOL, body[position:])
        if section is None:
            _reject()
        first, count = int(section[1]), int(section[2])
        if not 1 <= count <= inspection.limits.maximum_pdf_objects + 1:
            raise _Rejected("RV_LIMIT_EXCEEDED")
        if first + count > inspection.limits.maximum_pdf_objects + 1:
            raise _Rejected("RV_LIMIT_EXCEEDED")
        position += len(section[0])
        for number in range(first, first + count):
            if number == 0:
                zero_record = start + match.start(1) + position
            record = re.match(rb"([0-9]{10}) ([0-9]{5}) ([nf])(?: |\r\n|\r|\n)", body[position:])
            if record is None or number in numbers:
                _reject()
            position += len(record[0])
            # A conventional trailing space is followed by the line ending.
            if record[0].endswith(b" "):
                ending = re.match(_EOL, body[position:])
                if ending is None:
                    _reject()
                position += len(ending[0])
            offset, generation = int(record[1]), int(record[2])
            numbers.add(number)
            if record[3] == b"f":
                if number != 0 or offset != 0 or generation != 65535:
                    _reject()
            elif number < 1 or generation != 0 or offset < 1:
                _reject()
            else:
                entries[(number, 0)] = offset
    if not numbers or numbers != set(range(min(numbers), max(numbers) + 1)):
        _reject()
    if (0 in numbers) != (target != 0):
        _reject()
    return entries, _trailer(match[2], inspection), zero_record


def _auxiliary_dictionary(
    data: bytes, identity: _Identity, inspection: _Inspection
) -> dict[bytes, object]:
    lexer = _Lex(data, inspection)
    if [lexer.token() for _ in range(3)] != [
        ("number", str(identity[0]).encode()),
        ("number", b"0"),
        ("word", b"obj"),
    ] or lexer.token() != ("delimiter", b"<<"):
        _reject()
    values: dict[bytes, object] = {}
    while (token := lexer.token()) != ("delimiter", b">>"):
        if token[0] != "name" or token[1] in values:
            _reject()
        key = token[1]
        if key == b"/H":
            if lexer.token() != ("delimiter", b"["):
                _reject()
            values[key] = (_integer(lexer), _integer(lexer))
            if lexer.token() != ("delimiter", b"]"):
                _reject()
        elif key == b"/Filter":
            if lexer.token() != ("name", b"/FlateDecode"):
                _reject()
            values[key] = b"/FlateDecode"
        elif key in {
            b"/Linearized",
            b"/L",
            b"/O",
            b"/E",
            b"/N",
            b"/T",
            b"/P",
            b"/S",
            b"/I",
            b"/Length",
        }:
            values[key] = _integer(lexer)
        else:
            _reject()
    return values


def _physical_object(
    source: BinaryIO,
    document: pikepdf.Pdf,
    offset: int,
    end: int,
    identity: _Identity,
    inspection: _Inspection,
) -> tuple[bytes, int]:
    data = _read(source, offset, end, inspection)
    lexer = _Lex(data, inspection)
    if [lexer.token() for _ in range(3)] != [
        ("number", str(identity[0]).encode()),
        ("number", b"0"),
        ("word", b"obj"),
    ]:
        _reject()
    lexer.value()
    following = lexer.token()
    if following == ("word", b"stream"):
        item = document.get_object(identity)
        length: object = item.get("/Length") if isinstance(item, pikepdf.Stream) else None
        if not isinstance(item, pikepdf.Stream) or type(length) is not int or length < 0:
            _reject()
        ending = re.match(_EOL, data[lexer.position :])
        if ending is None:
            _reject()
        lexer.position += len(ending[0]) + length
        if lexer.position > len(data) or lexer.token() != ("word", b"endstream"):
            _reject()
        following = lexer.token()
    if following != ("word", b"endobj"):
        _reject()
    content_end = offset + lexer.position
    if lexer.token() != ("eof", b""):
        _reject()
    return data, content_end


class _Bits:
    def __init__(self, data: bytes, inspection: _Inspection) -> None:
        self.data, self.inspection, self.position = data, inspection, 0

    def read(self, width: int) -> int:
        self.inspection.checkpoint()
        if not 0 <= width <= 32 or self.position + width > len(self.data) * 8:
            _reject()
        value = 0
        for _ in range(width):
            value = (value << 1) | ((self.data[self.position // 8] >> (7 - self.position % 8)) & 1)
            self.position += 1
        return value

    def align(self) -> None:
        if self.read((-self.position) % 8) != 0:
            _reject()

    def row(self, count: int, width: int) -> list[int]:
        if not 0 <= count <= self.inspection.limits.maximum_pdf_objects:
            raise _Rejected("RV_LIMIT_EXCEEDED")
        result = [self.read(width) for _ in range(count)]
        self.align()
        return result

    def finish(self) -> None:
        self.align()
        if self.position != len(self.data) * 8:
            _reject()


def _hints(
    stream: pikepdf.Stream,
    dictionary: dict[bytes, object],
    document: pikepdf.Pdf,
    positions: list[tuple[int, _Identity]],
    sizes: dict[_Identity, int],
    hint_offset: int,
    hint_length: int,
    inspection: _Inspection,
) -> None:
    if set(dictionary) not in (
        {b"/S", b"/Length", b"/Filter"},
        {b"/S", b"/I", b"/Length", b"/Filter"},
        {b"/S", b"/Length"},
        {b"/S", b"/I", b"/Length"},
    ):
        _reject()
    data = inspection.decoded(stream)
    shared_at, info_at = dictionary[b"/S"], dictionary.get(b"/I", len(data))
    if (
        type(shared_at) is not int
        or type(info_at) is not int
        or not 0 < shared_at < info_at <= len(data)
    ):
        _reject()
    offsets = {identity: offset for offset, identity in positions}
    physical_offsets = [offset for offset, _ in positions]

    def real_offset(offset: int) -> int:
        return offset + hint_length if offset >= hint_offset else offset

    reader = _Bits(data[:shared_at], inspection)
    header = [reader.read(width) for width in (32, 32, 16, 32, 16, 32, 16, 32, 16, 16, 16, 16, 16)]
    pages = len(document.pages)
    if any(header[index] > 32 for index in (2, 4, 6, 8, 9, 10, 11)) or header[12] not in {1, 4, 8}:
        _reject()
    counts = reader.row(pages, header[2])
    lengths = reader.row(pages, header[4])
    shared_counts = reader.row(pages, header[9])
    total_shared = sum(shared_counts)
    identifiers = reader.row(total_shared, header[10])
    numerators = reader.row(total_shared, header[11])
    content_offsets = reader.row(pages, header[6])
    content_lengths = reader.row(pages, header[8])
    reader.finish()
    # These are optimization hints, not document-content containers. Acrobat
    # writes zero content offsets and repeats page-length deltas with a zero
    # minimum; qpdf repeats the complete page-length encoding. Neither style
    # describes the literal content-stream location. Admit only those closed
    # styles, rather than arbitrary bounded integers (PDF implementation notes
    # 126/127, also documented in qpdf's linearization implementation).
    if (
        header[5] != 0
        or header[6] != 0
        or any(content_offsets)
        or header[7] not in {0, header[3]}
        or header[8] != header[4]
        or content_lengths != lengths
        or any(numerators)
    ):
        _reject()
    at = real_offset(header[1])
    for index, page in enumerate(document.pages):
        inspection.checkpoint()
        length, count = header[3] + lengths[index], header[0] + counts[index]
        physical_count = bisect_left(physical_offsets, at + length) - bisect_left(
            physical_offsets, at
        )
        if (
            offsets.get(page.obj.objgen) != at
            or not 1 <= length <= inspection.limits.maximum_source_bytes
            or not 1 <= count <= inspection.limits.maximum_pdf_objects
            or physical_count != count
            or header[5] + content_offsets[index] > length
            or header[7] + content_lengths[index] > length
            or header[5] + content_offsets[index] + header[7] + content_lengths[index] > length
        ):
            _reject()
        at += length
    shared = _Bits(data[shared_at:info_at], inspection)
    first, first_offset, initial_count, total_count, count_width, minimum_length, length_width = (
        shared.read(width) for width in (32, 32, 32, 32, 16, 32, 16)
    )
    if not 0 <= initial_count <= total_count <= len(positions):
        raise _Rejected("RV_LIMIT_EXCEEDED")
    # The admitted Acrobat/qpdf style uses one physical object per group and
    # implements no signature payload (PDF implementation notes 125/128-130).
    if count_width != 0:
        _reject()
    lengths = shared.row(total_count, length_width)
    signatures = shared.row(total_count, 1)
    if any(signatures):
        _reject()
    counts = shared.row(total_count, count_width)
    shared.finish()
    if any(identifier >= total_count for identifier in identifiers):
        _reject()
    if total_count == initial_count:
        if first or first_offset:
            _reject()
    elif offsets.get((first, 0)) != real_offset(first_offset):
        _reject()
    number = document.pages[0].obj.objgen[0]
    for index in range(total_count):
        inspection.checkpoint()
        if index == initial_count:
            number = first
        count = counts[index] + 1
        if not 1 <= count <= inspection.limits.maximum_pdf_objects:
            raise _Rejected("RV_LIMIT_EXCEEDED")
        identities = [(number + delta, 0) for delta in range(count)]
        if (
            any(identity not in sizes for identity in identities)
            or sum(sizes[value] for value in identities) != minimum_length + lengths[index]
        ):
            _reject()
        number += count
    if info_at < len(data):
        generic = _Bits(data[info_at:], inspection)
        first, offset, count, length = (generic.read(32) for _ in range(4))
        generic.finish()
        info = document.trailer.get("/Info")
        identity = (first, 0)
        if (
            not isinstance(info, pikepdf.Dictionary)
            or info.objgen != identity
            or count != 1
            or offsets.get(identity) != real_offset(offset)
            or sizes.get(identity) != length
        ):
            _reject()


def inspect_linearized_envelope(
    source: BinaryIO, document: pikepdf.Pdf, inspection: _Inspection
) -> frozenset[_Identity]:
    """Prove one complete linearized physical file and return its two auxiliaries.

    The caller still inspects all reachable dictionaries, resource content and
    rendered output. Returned objects are closed structures inspected here,
    rather than ignored unreachable content. Input position is always restored.
    """
    original = source.tell()
    try:
        inspection.checkpoint()
        source.seek(0, 2)
        size = source.tell()
        if (
            not 1
            <= size
            <= min(inspection.limits.maximum_source_bytes, inspection.limits.maximum_output_bytes)
        ):
            raise _Rejected("RV_LIMIT_EXCEEDED")
        source.seek(0)
        overlap = max([len(value) for value in inspection.patterns] + [16]) - 1
        carried, consumed, eof_count, start_count = b"", 0, 0, 0
        while block := source.read(_CHUNK):
            combined = carried + block
            inspection.scan(combined)
            eof_count += sum(
                match.end() > len(carried) for match in re.finditer(rb"%%EOF", combined)
            )
            start_count += sum(
                match.end() > len(carried) for match in re.finditer(rb"startxref", combined)
            )
            carried, consumed = combined[-overlap:], consumed + len(block)
            if consumed > size:
                _reject()
        if consumed != size or eof_count != 2 or start_count != 2:
            raise _Rejected("RV_REVISION_RETAINED")
        tail = _read(source, max(0, size - 128), size, inspection)
        ending = re.search(rb"startxref\s+([0-9]+)\s+%%EOF[\x00\t\n\x0c\r ]*\Z", tail)
        if ending is None:
            raise _Rejected("RV_REVISION_RETAINED")
        front = int(ending[1])
        previous: object = document.trailer.get("/Prev")
        if type(previous) is not int or not 0 < front < previous < size:
            raise _Rejected("RV_REVISION_RETAINED")
        entries = document.get_xref_table()
        if not 2 <= len(entries) <= inspection.limits.maximum_pdf_objects or any(
            entry.type != 1 or type(entry.offset) is not int or identity[1] != 0
            for identity, entry in entries.items()
        ):
            _reject()
        positions = sorted(
            (int(entry.offset), identity)
            for identity, entry in entries.items()
            if entry.offset is not None
        )
        if len({offset for offset, _ in positions}) != len(positions):
            _reject()
        first_offset, first_identity = positions[0]
        if not 0 < first_offset < front < positions[1][0] < previous or first_identity[0] < 2:
            _reject()
        header = _read(source, 0, first_offset, inspection)
        if (
            re.fullmatch(
                rb"%PDF-[12]\.[0-9]"
                + _EOL
                + rb"%(?:\xbf\xf7\xa2\xfe|\xe2\xe3\xcf\xd3)"
                + _EOL
                + rb"[\x00\t\n\x0c\r ]*",
                header,
            )
            is None
        ):
            _reject()
        upper, first_trailer, _ = _xref(source, front, positions[1][0], 0, inspection)
        lower, final_trailer, zero_at = _xref(source, previous, size, front, inspection)
        if (
            set(lower) != {(number, 0) for number in range(1, first_identity[0])}
            or set(upper) != {(number, 0) for number in range(first_identity[0], len(entries) + 1)}
            or lower | upper != {identity: entry.offset for identity, entry in entries.items()}
            or final_trailer
            != _Trailer(first_identity[0], None, None, None, first_trailer.identifiers)
            or first_trailer.size != len(entries) + 1
            or first_trailer.previous != previous
            or first_trailer.root != document.Root.objgen
            or document.Root.objgen not in upper
            or document.pages[0].obj.objgen not in upper
            or first_trailer.info != getattr(document.trailer.get("/Info"), "objgen", None)
        ):
            _reject()
        data, linear_end = _physical_object(
            source, document, first_offset, front, first_identity, inspection
        )
        if linear_end > 1024:
            _reject()
        linear = _auxiliary_dictionary(data, first_identity, inspection)
        if set(linear) not in (
            {b"/Linearized", b"/L", b"/H", b"/O", b"/E", b"/N", b"/T"},
            {b"/Linearized", b"/L", b"/H", b"/O", b"/E", b"/N", b"/T", b"/P"},
        ):
            _reject()
        if (
            linear[b"/Linearized"] != 1
            or linear[b"/L"] != size
            or linear[b"/T"] != zero_at - 1
            or linear[b"/N"] != len(document.pages)
            or linear.get(b"/P", 0) != 0
            or linear[b"/O"] != document.pages[0].obj.objgen[0]
            or linear[b"/E"] != lower.get((1, 0))
        ):
            _reject()
        hint = linear[b"/H"]
        if not isinstance(hint, tuple) or len(hint) != 2:
            _reject()
        hint_offset, hint_length = hint
        if (
            type(hint_offset) is not int
            or type(hint_length) is not int
            or not front < hint_offset < cast(int, linear[b"/E"])
        ):
            _reject()
        hint_identity = next(
            (identity for offset, identity in positions if offset == hint_offset), None
        )
        if (
            hint_identity is None
            or hint_identity not in upper
            or not upper[document.Root.objgen] < upper[document.pages[0].obj.objgen]
            or not hint_offset < upper[document.pages[0].obj.objgen]
        ):
            _reject()
        sizes: dict[_Identity, int] = {}
        hint_data, hint_end, hint_boundary = b"", 0, 0
        for index, (offset, identity) in enumerate(positions[1:], 1):
            end = positions[index + 1][0] if index + 1 < len(positions) else previous
            data, content_end = _physical_object(
                source, document, offset, end, identity, inspection
            )
            sizes[identity] = end - offset
            if identity == hint_identity:
                hint_data, hint_end, hint_boundary = data, content_end, end
        if not hint_end - hint_offset <= hint_length <= hint_boundary - hint_offset:
            _reject()
        stream = document.get_object(hint_identity)
        if not isinstance(stream, pikepdf.Stream):
            _reject()
        _hints(
            stream,
            _auxiliary_dictionary(hint_data, hint_identity, inspection),
            document,
            positions,
            sizes,
            hint_offset,
            hint_length,
            inspection,
        )
        return frozenset({first_identity, hint_identity})
    finally:
        source.seek(original)
        if source.tell() != original:
            raise _Rejected("RV_METHOD_FAILED")

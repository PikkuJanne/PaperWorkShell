"""Closed, inert metadata permitted only by the viewer-resave verifier.

The original export profile never calls this adapter.  The accepted XML is the
observed Adobe serialization, with only valid dates and random UUID values
variable.  Its namespaces, toolkit, layout and padding are fixed, so whitespace,
comments and ignored extension properties cannot become uninspected carriers.
"""

from __future__ import annotations

import hashlib
import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime

import pikepdf

from paperworkshell.pdf.redaction_verification_structure import _Inspection, _Rejected

_META = "{adobe:ns:meta/}"
_RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
_XMP = "{http://ns.adobe.com/xap/1.0/}"
_MM = "{http://ns.adobe.com/xap/1.0/mm/}"
_DC = "{http://purl.org/dc/elements/1.1/}"
_TOOLKIT = "Adobe XMP Core 9.1-c001 79.675d0f7, 2023/06/11-19:21:16        "
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[+-][0-9]{2}:[0-9]{2}")
_UUID = re.compile(r"uuid:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


@dataclass(frozen=True, slots=True)
class ViewerMetadataEvidence:
    auxiliary_objgens: frozenset[tuple[int, int]]
    info_objgen: tuple[int, int] | None = None
    metadata_objgen: tuple[int, int] | None = None


def _serialized(values: dict[str, str]) -> bytes:
    # This exact padding is written by the observed viewer, not arbitrary XML
    # whitespace.  Comparing it closes an otherwise unchecked storage channel.
    text = (
        '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        f'<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="{_TOOLKIT}">\n'
        '   <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '      <rdf:Description rdf:about=""\n'
        '            xmlns:xmp="http://ns.adobe.com/xap/1.0/"\n'
        '            xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/"\n'
        '            xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f"         <xmp:ModifyDate>{values['ModifyDate']}</xmp:ModifyDate>\n"
        f"         <xmp:CreateDate>{values['CreateDate']}</xmp:CreateDate>\n"
        f"         <xmp:MetadataDate>{values['MetadataDate']}</xmp:MetadataDate>\n"
        f"         <xmpMM:DocumentID>{values['DocumentID']}</xmpMM:DocumentID>\n"
        f"         <xmpMM:InstanceID>{values['InstanceID']}</xmpMM:InstanceID>\n"
        "         <dc:format>application/pdf</dc:format>\n"
        "      </rdf:Description>\n"
        "   </rdf:RDF>\n"
        "</x:xmpmeta>\n"
    )
    return (text + (" " * 100 + "\n") * 20 + " " * 27 + '\n<?xpacket end="w"?>').encode()


def _scan(data: bytes, inspection: _Inspection) -> None:
    inspection.scan(data)
    if hashlib.sha256(data).hexdigest() in inspection.hashes:
        raise _Rejected("RV_ORIGINAL_OBJECT_RETAINED")


def _xml_values(data: bytes, inspection: _Inspection) -> dict[str, str]:
    # Length is determined by the closed grammar, not an additional processing
    # budget.  No DTD/entity or arbitrary XML is passed to the parser.
    if len(data) != 2989 or b"<!" in data or b"&" in data:
        raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise _Rejected("RV_STRUCTURE_UNSUPPORTED") from None
    nodes = list(root.iter())
    expected = [
        _META + "xmpmeta",
        _RDF + "RDF",
        _RDF + "Description",
        _XMP + "ModifyDate",
        _XMP + "CreateDate",
        _XMP + "MetadataDate",
        _MM + "DocumentID",
        _MM + "InstanceID",
        _DC + "format",
    ]
    if [node.tag for node in nodes] != expected:
        raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
    values: dict[str, str] = {}
    for index, node in enumerate(nodes):
        inspection.node(index)
        attributes = (
            {_META + "xmptk": _TOOLKIT}
            if index == 0
            else {_RDF + "about": ""}
            if index == 2
            else {}
        )
        if node.attrib != attributes:
            raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
        if index >= 3:
            if list(node) or node.text is None:
                raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
            values[node.tag.rsplit("}", 1)[1]] = node.text
            _scan(node.text.encode(), inspection)
    if data != _serialized(values):
        raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
    if values["format"] != "application/pdf":
        raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
    return values


def validate_viewer_metadata(
    document: pikepdf.Pdf, inspection: _Inspection
) -> ViewerMetadataEvidence:
    """Validate every auxiliary byte and return only the authenticated objects.

    Callers may exempt these exact objects from the ordinary resource grammar.
    Their physical bytes and references still require the envelope inspection.
    No PDF objects or input bytes are rewritten.
    """
    info = document.trailer.get("/Info")
    metadata = document.Root.get("/Metadata")
    if info is None and metadata is None:
        return ViewerMetadataEvidence(frozenset())
    if (
        not isinstance(info, pikepdf.Dictionary)
        or info.objgen == (0, 0)
        or set(map(str, info.keys())) != {"/CreationDate", "/ModDate"}
        or not isinstance(metadata, pikepdf.Stream)
        or metadata.objgen == (0, 0)
        or metadata.objgen == info.objgen
        or set(map(str, metadata.keys())) - {"/Type", "/Subtype", "/Length", "/Filter"}
        or metadata.get("/Type") != pikepdf.Name.Metadata
        or metadata.get("/Subtype") != pikepdf.Name.XML
    ):
        raise _Rejected("RV_FEATURE_RETAINED")
    values = _xml_values(inspection.decoded(metadata), inspection)
    dates: dict[str, datetime] = {}
    for field in ("ModifyDate", "CreateDate", "MetadataDate"):
        text = values[field]
        if _DATE.fullmatch(text) is None:
            raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
        try:
            date = datetime.fromisoformat(text)
        except ValueError:
            raise _Rejected("RV_STRUCTURE_UNSUPPORTED") from None
        if date.isoformat(timespec="seconds") != text:
            raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
        dates[field] = date
        _scan(date.strftime("%Y%m%d%H%M%S%z").encode(), inspection)
        _scan(
            bytes(
                [
                    date.year // 100,
                    date.year % 100,
                    date.month,
                    date.day,
                    date.hour,
                    date.minute,
                    date.second,
                ]
            ),
            inspection,
        )
        for order in ("big", "little"):
            _scan(int(date.timestamp()).to_bytes(8, order, signed=True), inspection)
    if dates["CreateDate"] > dates["ModifyDate"] or values["MetadataDate"] != values["ModifyDate"]:
        raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
    for key, field in (("/CreationDate", "CreateDate"), ("/ModDate", "ModifyDate")):
        value = info[key]
        if not isinstance(value, pikepdf.String):
            raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
        date = dates[field]
        offset = date.strftime("%z")
        expected = "D:" + date.strftime("%Y%m%d%H%M%S") + offset[:3] + "'" + offset[3:] + "'"
        _scan(bytes(value), inspection)
        _scan(str(value).encode(), inspection)
        if bytes(value) != expected.encode():
            raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
    identifiers: list[uuid.UUID] = []
    for field in ("DocumentID", "InstanceID"):
        text = values[field]
        if _UUID.fullmatch(text) is None:
            raise _Rejected("RV_STRUCTURE_UNSUPPORTED")
        identifier = uuid.UUID(text[5:])
        identifiers.append(identifier)
        _scan(identifier.bytes, inspection)
        _scan(identifier.bytes_le, inspection)
        _scan(identifier.hex.encode(), inspection)
    # Individually valid UUIDs must not split a known removed value at the
    # field boundary. Inspect both orders and both supported byte layouts.
    for first, second in (identifiers, list(reversed(identifiers))):
        _scan(first.bytes + second.bytes, inspection)
        _scan(first.bytes_le + second.bytes_le, inspection)
        _scan((first.hex + second.hex).encode(), inspection)
    inspection.checkpoint()
    return ViewerMetadataEvidence(
        frozenset({info.objgen, metadata.objgen}), info.objgen, metadata.objgen
    )

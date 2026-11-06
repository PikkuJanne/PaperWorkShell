"""Viewer bookkeeping is closed; numeric equivalence never covers resources."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import replace
from decimal import Decimal
from io import BytesIO

import pikepdf
import pytest

from paperworkshell.pdf.redaction_verification_resources import (
    _viewer_coordinate,
    verify_resource_identity,
)
from paperworkshell.pdf.redaction_verification_structure import _Inspection, _Rejected
from paperworkshell.pdf.redaction_verification_viewer_metadata import (
    _serialized,
    validate_viewer_metadata,
)
from tests.security.test_redaction_verification_corpus import _limits
from tests.unit.test_redaction_sanitization_adapter import _Control

_VALUES = {
    "ModifyDate": "2026-10-17T12:01:02+02:00",
    "CreateDate": "2026-10-17T12:00:00+02:00",
    "MetadataDate": "2026-10-17T12:01:02+02:00",
    "DocumentID": "uuid:00000000-0000-4000-8000-000000000001",
    "InstanceID": "uuid:00000000-0000-4000-8000-000000000002",
}


def _save(document: pikepdf.Pdf) -> bytes:
    output = BytesIO()
    document.save(
        output,
        deterministic_id=True,
        object_stream_mode=pikepdf.ObjectStreamMode.disable,
        fix_metadata_version=False,
    )
    return output.getvalue()


def _reference() -> bytes:
    with pikepdf.Pdf.new() as document:
        page = document.add_blank_page(page_size=(246, 77))
        page.MediaBox = pikepdf.Array(
            [0, 0, Decimal("245.759994506836"), Decimal("76.8000030517578")]
        )
        page.Contents = pikepdf.Stream(document, b"0 0 1 rg 0 0 10.12345678901234 10 re f\n")
        # The unused binary stream must be authenticated despite not painting.
        page.Resources.XObject = pikepdf.Dictionary(
            Unused=pikepdf.Stream(
                document,
                b"\0\1\2\3",
                Type=pikepdf.Name.XObject,
                Subtype=pikepdf.Name.Image,
                Width=2,
                Height=2,
                ColorSpace=pikepdf.Name.DeviceGray,
                BitsPerComponent=8,
            )
        )
        return _save(document)


def _metadata(document: pikepdf.Pdf, values: dict[str, str] | None = None) -> None:
    document.Root.Metadata = pikepdf.Stream(
        document,
        _serialized(values or _VALUES),
        Type=pikepdf.Name.Metadata,
        Subtype=pikepdf.Name.XML,
    )
    document.docinfo["/CreationDate"] = "D:20261017120000+02'00'"
    document.docinfo["/ModDate"] = "D:20261017120102+02'00'"


def _viewer(reference: bytes) -> bytes:
    with pikepdf.Pdf.open(BytesIO(reference)) as document:
        _metadata(document)
        page = document.pages[0]
        page.MediaBox = pikepdf.Array([0, 0, Decimal("245.7599945068"), Decimal("76.80000305175")])
        page.CropBox = pikepdf.Array(list(page.MediaBox))
        page.Rotate = 0
        return _save(document)


def test_closed_viewer_roundtrip_passes_without_weakening_canonical_profile() -> None:
    reference = _reference()
    saved = _viewer(reference)
    with pikepdf.Pdf.open(BytesIO(saved)) as document:
        resaved = _save(document)
    for content in (saved, resaved):
        candidate, trusted = BytesIO(content), BytesIO(reference)
        candidate.seek(7)
        trusted.seek(9)
        result = verify_resource_identity(
            candidate, trusted, _limits(), _Control(), viewer_resave=True
        )
        assert result.passed, result.code
        assert candidate.tell() == 7 and trusted.tell() == 9
        assert candidate.getvalue() == content and trusted.getvalue() == reference
        strict = verify_resource_identity(
            BytesIO(content), BytesIO(reference), _limits(), _Control()
        )
        assert not strict.passed, "VIEWER_EQUIVALENCE_ESCAPED_TO_EXPORT_PROFILE"


def test_viewer_profile_also_accepts_unchanged_canonical_resources() -> None:
    content = _reference()
    assert verify_resource_identity(
        BytesIO(content), BytesIO(content), _limits(), _Control(), viewer_resave=True
    ).passed


@pytest.mark.parametrize(
    "actual,reference,accepted",
    [
        ("245.7599945068", "245.759994506836", True),
        ("76.80000305175", "76.8000030517578", True),
        ("-245.7599945068", "-245.759994506836", True),
        ("245.7599945068001", "245.759994506836", False),
        ("245.7599945069", "245.759994506836", False),
        ("76.80000305176", "76.8000030517578", False),
        ("245.7599945067", "245.759994506836", False),
        ("245.7599945", "245.759994506836", False),
        ("1E-20", "1.00000000000001E-20", False),
        ("9.123456789012", "9.12345678901234", False),
        ("1234.123456789", "1234.123456789012", False),
        ("1E+2", "100", True),
        ("0", "0", True),
    ],
)
def test_geometry_accepts_only_exact_observed_decimal_truncation(
    actual: str, reference: str, accepted: bool
) -> None:
    assert _viewer_coordinate(Decimal(actual), Decimal(reference)) is accepted


@pytest.mark.parametrize(
    "kind",
    [
        "crop",
        "rotate",
        "boolean_rotate",
        "width",
        "long_digits",
        "user_unit",
        "content_number",
        "unused_resource",
        "resource_key",
        "catalog_key",
        "page_tree_key",
    ],
)
def test_viewer_equivalence_rejects_all_other_geometry_and_resource_changes(kind: str) -> None:
    reference = _reference()
    with pikepdf.Pdf.open(BytesIO(_viewer(reference))) as document:
        page = document.pages[0]
        if kind == "crop":
            page.CropBox = pikepdf.Array([0, 0, 200, 70])
        elif kind == "rotate":
            page.Rotate = 360
        elif kind == "boolean_rotate":
            page.Rotate = False
        elif kind == "width":
            page.MediaBox[2] = Decimal("245.7599945069")
            page.CropBox[2] = Decimal("245.7599945069")
        elif kind == "long_digits":
            page.MediaBox = pikepdf.Object.parse(b"[0 0 245.759994506800001 76.80000305175]")
            page.CropBox = pikepdf.Object.parse(page.MediaBox.unparse())
        elif kind == "user_unit":
            page.UserUnit = 1
        elif kind == "content_number":
            page.Contents.write(b"0 0 1 rg 0 0 10.12345678901 10 re f\n")
        elif kind == "unused_resource":
            page.Resources.XObject.Unused.write(b"\0\1\2\4")
        elif kind == "resource_key":
            page.Resources.XObject.Unused.Hidden = pikepdf.String("SYNTHETIC UNBOUND")
        elif kind == "catalog_key":
            document.Root.Resources = page.Resources
        else:
            document.Root.Pages.Resources = page.Resources
        altered = _save(document)
    result = verify_resource_identity(
        BytesIO(altered), BytesIO(reference), _limits(), _Control(), viewer_resave=True
    )
    assert not result.passed, "VIEWER_RESOURCE_MUTATION_ACCEPTED"


@pytest.mark.parametrize(
    "kind",
    [
        "info_extra",
        "info_date",
        "metadata_extra",
        "metadata_type",
        "namespace",
        "attribute",
        "element",
        "comment",
        "entity",
        "dtd",
        "processing_instruction",
        "padding",
        "toolkit",
        "date_invalid",
        "date_mismatch",
        "date_order",
        "uuid_version",
        "uuid_case",
        "uuid_canary",
        "info_utf16",
    ],
)
def test_metadata_has_no_unchecked_extension_or_padding_channel(kind: str) -> None:
    reference = _reference()
    with pikepdf.Pdf.open(BytesIO(_viewer(reference))) as document:
        stream = document.Root.Metadata
        data = stream.read_bytes()
        values = dict(_VALUES)
        if kind == "info_extra":
            document.docinfo.Title = "SYNTHETIC UNBOUND"
        elif kind == "info_date":
            document.docinfo.ModDate = "SYNTHETIC UNBOUND"
        elif kind == "metadata_extra":
            stream.Hidden = pikepdf.String("SYNTHETIC UNBOUND")
        elif kind == "metadata_type":
            stream.Subtype = pikepdf.Name.Text
        elif kind == "namespace":
            data = data.replace(b"adobe:ns:meta/", b"adobe:ns:other/")
        elif kind == "attribute":
            data = data.replace(b'rdf:about=""', b'rdf:about="SYNTHETIC"')
        elif kind == "element":
            data = data.replace(b"ModifyDate", b"HiddenDate")
        elif kind == "comment":
            data = data.replace(b"</x:xmpmeta>", b"<!--SYNTHETIC--></x:xmpmeta>")
        elif kind == "entity":
            data = data.replace(b"application/pdf", b"application&#47;pdf")
        elif kind == "dtd":
            data = b'<!DOCTYPE x [<!ENTITY x SYSTEM "file:///synthetic">]>' + data
        elif kind == "processing_instruction":
            data = data.replace(b"</x:xmpmeta>", b"<?hidden synthetic?></x:xmpmeta>")
        elif kind == "padding":
            data = data[:-19] + data[-19:].replace(b"\n", b"\t", 1)
            data = data.replace(b" " * 100, b"\t" + b" " * 99, 1)
        elif kind == "toolkit":
            data = data.replace(b"Adobe XMP Core", b"Other XMP Core")
        elif kind in {"date_invalid", "date_mismatch", "date_order"}:
            values["CreateDate"] = {
                "date_invalid": "2026-19-17T12:00:00+02:00",
                "date_mismatch": "2026-10-17T12:00:01+02:00",
                "date_order": "2026-10-17T12:02:00+02:00",
            }[kind]
            data = _serialized(values)
        elif kind == "uuid_version":
            values["DocumentID"] = values["DocumentID"].replace("-4000-", "-1000-")
            data = _serialized(values)
        elif kind == "uuid_case":
            values["DocumentID"] = "uuid:AAAAAAAA-0000-4000-8000-000000000001"
            data = _serialized(values)
        elif kind == "uuid_canary":
            values["DocumentID"] = "uuid:" + str(uuid.UUID(bytes=b"PWSRED\x40\0\x80" + b"\0" * 7))
            data = _serialized(values)
        else:
            date = str(document.docinfo.ModDate)
            document.docinfo.ModDate = pikepdf.String(b"\xfe\xff" + date.encode("utf-16-be"))
        stream.write(data)
        inspection = _Inspection(("PWSRED",), (), _limits(), _Control())
        with pytest.raises(_Rejected) as raised:
            validate_viewer_metadata(document, inspection)
        assert raised.value.code in {
            "RV_FEATURE_RETAINED",
            "RV_STRUCTURE_UNSUPPORTED",
            "RV_CANARY_RECOVERED",
        }


def test_metadata_auxiliary_ids_are_exact_and_source_fingerprints_are_checked() -> None:
    with pikepdf.Pdf.open(BytesIO(_viewer(_reference()))) as document:
        inspection = _Inspection((), (), _limits(), _Control())
        evidence = validate_viewer_metadata(document, inspection)
        assert evidence.auxiliary_objgens == frozenset(
            {document.Root.Metadata.objgen, document.trailer.Info.objgen}
        )
        for content in (
            document.Root.Metadata.read_bytes(),
            bytes(document.trailer.Info.CreationDate),
            uuid.UUID(_VALUES["DocumentID"][5:]).bytes,
        ):
            inspection = _Inspection(
                (), (hashlib.sha256(content).hexdigest(),), _limits(), _Control()
            )
            with pytest.raises(_Rejected) as raised:
                validate_viewer_metadata(document, inspection)
            assert raised.value.code == "RV_ORIGINAL_OBJECT_RETAINED"


@pytest.mark.parametrize("failure", ["memory", "objects", "cancel", "timeout"])
def test_viewer_resource_comparison_preserves_limits_and_failure_paths(failure: str) -> None:
    reference = _reference()
    limits, control = _limits(), _Control()
    if failure == "memory":
        limits = replace(limits, maximum_memory_bytes=16)
    elif failure == "objects":
        limits = replace(limits, maximum_pdf_objects=1)
    elif failure == "cancel":
        control = _Control(cancel_at=1)
    else:
        control = _Control(expired=True)
    result = verify_resource_identity(
        BytesIO(_viewer(reference)), BytesIO(reference), limits, control, viewer_resave=True
    )
    assert not result.passed
    assert result.code in {"RV_CANCELLED", "RV_TIMED_OUT", "RV_LIMIT_EXCEEDED"}

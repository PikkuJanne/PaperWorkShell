"""Independent final-candidate structure checks, with synthetic failure controls.

PYTEST_DONT_REWRITE
"""

from __future__ import annotations

import hashlib
import zlib
from dataclasses import replace
from io import BytesIO
from typing import BinaryIO, cast

import pikepdf
import pytest

from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.pdf.redaction_reconstruction import RedactionReconstructionLimits
from paperworkshell.pdf.redaction_verification_structure import (
    StructuralEvidence,
    inspect_structure,
)

_CANARY = "PWS-SYNTHETIC-STRUCTURE-CANARY"


class _Control:
    def __init__(self, *, cancelled: bool = False, expired: bool = False) -> None:
        self.cancelled, self.expired = cancelled, expired

    def cancellation_requested(self) -> bool:
        return self.cancelled

    def deadline_expired(self) -> bool:
        return self.expired


def _limits() -> RedactionReconstructionLimits:
    return RedactionReconstructionLimits.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1)


def _candidate(
    stream: bytes = b"q\nQ",
    *,
    extra: bytes | None = None,
    page_extra: bytes = b"",
    filter_name: bytes = b"/FlateDecode",
    raw_stream: bytes | None = None,
) -> bytes:
    encoded = zlib.compress(stream) if raw_stream is None else raw_stream
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 72 48] /Resources << >> "
        b"/Contents 4 0 R " + page_extra + b" >>",
        b"<< /Length "
        + str(len(encoded)).encode()
        + b" /Filter "
        + filter_name
        + b" >>\nstream\n"
        + encoded
        + b"\nendstream",
    ]
    if extra is not None:
        objects.append(extra)
    output, offsets = bytearray(b"%PDF-1.7\n%\xbf\xf7\xa2\xfe\n"), []
    for index, value in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(str(index).encode() + b" 0 obj\n" + value + b"\nendobj\n")
    xref = len(output)
    output.extend(b"xref\n0 " + str(len(objects) + 1).encode() + b"\n0000000000 65535 f \n")
    for offset in offsets:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        b"trailer\n<< /Root 1 0 R /Size "
        + str(len(objects) + 1).encode()
        + b" >>\nstartxref\n"
        + str(xref).encode()
        + b"\n%%EOF\n"
    )
    return bytes(output)


def _inspect(
    content: bytes,
    *,
    forbidden: tuple[str, ...] = (_CANARY,),
    hashes: tuple[str, ...] = (),
    limits: RedactionReconstructionLimits | None = None,
    control: _Control | None = None,
) -> StructuralEvidence:
    source = BytesIO(content)
    source.seek(9)
    result = inspect_structure(
        source,
        forbidden_tokens=forbidden,
        forbidden_stream_sha256=hashes,
        limits=limits or _limits(),
        control=control or _Control(),
    )
    assert source.tell() == 9, "RV_POSITION_CHANGED"
    assert source.getvalue() == content, "RV_SOURCE_CHANGED"
    return result


@pytest.mark.parametrize(
    "field",
    [
        b"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >>",
        b"/ID [<0011> <0022>]",
        b"/ID [<< /Type /Font >>]",
        b"/Size 7",
    ],
)
def test_trailer_cannot_become_an_extra_resource_or_value_container(field: bytes) -> None:
    content = _candidate()
    if field.startswith(b"/Size"):
        content = content.replace(b"/Size 5", field)
    else:
        content = content.replace(b"/Size 5 >>", b"/Size 5 " + field + b" >>")
    result = _inspect(content, forbidden=())
    assert not result.passed, "RV_TRAILER_ROLE_NOT_CLOSED"


def test_canonical_final_candidate_passes_independent_complete_envelope() -> None:
    result = _inspect(_candidate())
    assert result == StructuralEvidence(True, "RV_OBJECTS_CLEAR", 4, 3), "RV_SAFE_FAILED"


def test_real_qpdf_classic_save_and_non_sensitive_text_pass() -> None:
    with pikepdf.Pdf.new() as document:
        page = document.add_blank_page(page_size=(72, 48))
        page.Resources = pikepdf.Dictionary(
            Font=pikepdf.Dictionary(
                A=pikepdf.Dictionary(
                    Type=pikepdf.Name.Font,
                    Subtype=pikepdf.Name.Type1,
                    BaseFont=pikepdf.Name.Helvetica,
                )
            )
        )
        page.Contents = pikepdf.Stream(document, b"BT /A 8 Tf 4 20 Td (KEEP SYNTHETIC) Tj ET")
        target = BytesIO()
        document.save(
            target, deterministic_id=True, object_stream_mode=pikepdf.ObjectStreamMode.disable
        )
    assert _inspect(target.getvalue()).passed, "RV_QPDF_FAILED"


@pytest.mark.parametrize("encoding", ["plain", "octal", "hex", "utf16be", "utf16le"])
def test_decoded_content_canary_controls_fail_for_supported_string_encodings(encoding: str) -> None:
    data = _CANARY.encode()
    if encoding == "octal":
        operand = b"(" + b"".join(f"\\{value:03o}".encode() for value in data) + b")"
    elif encoding == "hex":
        operand = b"<" + data.hex().encode() + b">"
    elif encoding.startswith("utf16"):
        data = _CANARY.encode("utf-16-be" if encoding == "utf16be" else "utf-16-le")
        operand = b"<" + data.hex().encode() + b">"
    else:
        operand = b"(" + data + b")"
    result = _inspect(_candidate(b"BT " + operand + b" Tj ET"))
    assert result.code == "RV_CANARY_RECOVERED" and not result.passed, "RV_CANARY_MISSED"
    assert _CANARY not in repr(result), "RV_REPORT_LEAKED"


def test_reachable_form_xobject_has_independent_escaped_string_scanning() -> None:
    escaped = b"".join(f"\\{value:03o}".encode() for value in _CANARY.encode())
    with pikepdf.Pdf.new() as document:
        page = document.add_blank_page(page_size=(72, 48))
        form = pikepdf.Stream(document, b"BT (" + escaped + b") Tj ET")
        form.Type, form.Subtype = pikepdf.Name.XObject, pikepdf.Name.Form
        form.BBox = pikepdf.Array([0, 0, 72, 48])
        form.Resources = pikepdf.Dictionary()
        page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Fm0=form))
        page.Contents = pikepdf.Stream(document, b"q /Fm0 Do Q")
        target = BytesIO()
        document.save(
            target, deterministic_id=True, object_stream_mode=pikepdf.ObjectStreamMode.disable
        )
    result = _inspect(target.getvalue())
    assert result.code == "RV_CANARY_RECOVERED", "RV_FORM_CANARY_MISSED"


def test_exact_affected_original_decoded_stream_is_rejected() -> None:
    stream = b"q\nQ"
    result = _inspect(_candidate(stream), hashes=(hashlib.sha256(stream).hexdigest(),))
    assert result.code == "RV_ORIGINAL_OBJECT_RETAINED", "RV_ORIGINAL_STREAM_MISSED"


@pytest.mark.parametrize(
    "feature",
    [
        b"/Annots []",
        b"/AA << >>",
        b"/Metadata 5 0 R",
        b"/AF []",
        b"/ActualText (HIDDEN)",
        b"/AcroForm << /Fields [] >>",
        b"/Thumb 5 0 R",
        b"/OCProperties << >>",
    ],
)
def test_prohibited_carrier_is_rejected_even_without_a_known_canary(feature: bytes) -> None:
    result = _inspect(_candidate(page_extra=feature, extra=b"<< >>"))
    assert not result.passed and result.code == "RV_FEATURE_RETAINED", "RV_CARRIER_MISSED"


def test_unknown_dictionary_field_fails_closed() -> None:
    result = _inspect(_candidate(page_extra=b"/VendorPayload (UNKNOWN)"))
    assert result.code == "RV_STRUCTURE_UNSUPPORTED", "RV_UNKNOWN_ALLOWED"


@pytest.mark.parametrize(
    "stream",
    [
        b"BI /W 1 /H 1 /BPC 8 /CS /G ID X EI",
        b"/Span << /ActualText (HIDDEN) >> BDC EMC",
    ],
)
def test_inline_images_and_marked_content_payloads_are_unsupported(stream: bytes) -> None:
    result = _inspect(_candidate(stream))
    assert result.code == "RV_STRUCTURE_UNSUPPORTED", "RV_OPAQUE_CONTENT_ALLOWED"


def test_duplicate_dictionary_field_fails_before_native_last_value_semantics() -> None:
    result = _inspect(_candidate(page_extra=b"/Rotate 0 /R#6ftate 90"))
    assert not result.passed, "RV_DUPLICATE_KEY_ALLOWED"


def test_indexed_orphan_object_is_rejected_without_requiring_a_canary_match() -> None:
    result = _inspect(_candidate(extra=b"(ORIGINAL UNREACHABLE VALUE)"))
    assert result.code == "RV_STRUCTURE_UNSUPPORTED", "RV_ORPHAN_ALLOWED"


def test_unindexed_compressed_object_before_xref_cannot_escape_inspection() -> None:
    content = _candidate()
    insertion = b"99 0 obj\n<< /Length 10 >>\nstream\nHIDDEN12345\nendstream\nendobj\n"
    at = content.index(b"xref\n")
    modified = content[:at] + insertion + content[at:]
    modified = modified.replace(
        b"startxref\n" + str(at).encode(), b"startxref\n" + str(at + len(insertion)).encode()
    )
    result = _inspect(modified)
    assert not result.passed, "RV_UNINDEXED_OBJECT_ALLOWED"


@pytest.mark.parametrize("suffix", [b"TRAILING", b"\n%%EOF\n", b"\nstartxref\n0\n%%EOF\n"])
def test_trailing_bytes_and_additional_revision_markers_fail(suffix: bytes) -> None:
    result = _inspect(_candidate() + suffix)
    assert not result.passed and result.code == "RV_REVISION_RETAINED", "RV_REVISION_ALLOWED"


def test_previous_revision_trailer_key_fails() -> None:
    content = _candidate().replace(b"/Root 1 0 R /Size", b"/Prev 1 /Root 1 0 R /Size")
    assert not _inspect(content).passed, "RV_PREVIOUS_REVISION_ALLOWED"


@pytest.mark.parametrize("filter_name", [b"/LZWDecode", b"[/FlateDecode]", b"/DCTDecode"])
def test_unknown_or_unsupported_filter_fails_closed(filter_name: bytes) -> None:
    result = _inspect(_candidate(filter_name=filter_name))
    assert not result.passed, "RV_UNKNOWN_FILTER_ALLOWED"


@pytest.mark.parametrize(
    "compressed", [b"bad compressed stream", zlib.compress(b"q Q") + b"HIDDEN"]
)
def test_invalid_or_trailing_compressed_payload_fails(compressed: bytes) -> None:
    result = _inspect(_candidate(raw_stream=compressed))
    assert not result.passed, "RV_DECODE_FAILURE_ALLOWED"


def test_expansion_is_bounded_before_large_native_decode() -> None:
    result = _inspect(
        _candidate(b"A" * 100_000), limits=replace(_limits(), maximum_memory_bytes=64 * 1024)
    )
    assert result.code == "RV_LIMIT_EXCEEDED", "RV_EXPANSION_ALLOWED"


@pytest.mark.parametrize(
    "budget", ["maximum_source_bytes", "maximum_output_bytes", "maximum_pdf_objects"]
)
def test_configured_budget_fails_closed(budget: str) -> None:
    result = _inspect(_candidate(), limits=replace(_limits(), **{budget: 1}))
    assert result.code == "RV_LIMIT_EXCEEDED", "RV_BUDGET_IGNORED"


@pytest.mark.parametrize(
    "control,expected",
    [
        (_Control(cancelled=True), "RV_CANCELLED"),
        (_Control(expired=True), "RV_TIMED_OUT"),
    ],
)
def test_cancellation_and_deadline_have_closed_codes(control: _Control, expected: str) -> None:
    result = _inspect(_candidate(), control=control)
    assert result.code == expected and not result.passed, "RV_CONTROL_IGNORED"


def test_native_parse_failure_is_reported_without_input_or_exception_values() -> None:
    result = _inspect(b"%PDF-1.7\n" + _CANARY.encode() + b"\nstartxref\n1\n%%EOF\n")
    assert not result.passed and _CANARY not in repr(result), "RV_FAILURE_REPORT_LEAKED"


def test_result_fails_when_the_input_cannot_restore_its_position() -> None:
    class Unrestorable(BytesIO):
        def __init__(self, content: bytes) -> None:
            super().__init__(content)
            super().seek(9)

        def seek(self, offset: int, whence: int = 0) -> int:
            if offset == 9 and whence == 0:
                raise OSError("SYNTHETIC PRIVATE PATH")
            return super().seek(offset, whence)

    source: BinaryIO = Unrestorable(_candidate())
    result = inspect_structure(
        source,
        forbidden_tokens=(),
        forbidden_stream_sha256=(),
        limits=_limits(),
        control=_Control(),
    )
    assert result.code == "RV_METHOD_FAILED", "RV_POSITION_RESTORE_FAILURE_ALLOWED"


@pytest.mark.parametrize(
    "tokens,hashes",
    [
        ([_CANARY], ()),
        ((_CANARY, 1), ()),
        (("",), ()),
        (("X" * 1025,), ()),
        ((), ["0" * 64]),
        ((), ("0" * 63,)),
        ((), ("G" * 64,)),
        ((), (None,)),
    ],
)
def test_hostile_parameter_types_and_values_fail_closed(tokens: object, hashes: object) -> None:
    result = inspect_structure(
        BytesIO(_candidate()),
        forbidden_tokens=cast(tuple[str, ...], tokens),
        forbidden_stream_sha256=cast(tuple[str, ...], hashes),
        limits=_limits(),
        control=_Control(),
    )
    assert not result.passed and result.code == "RV_STRUCTURE_UNSUPPORTED", "RV_BAD_INPUT_ALLOWED"


def test_cumulative_canary_materialization_is_bounded_by_policy() -> None:
    result = _inspect(
        _candidate(),
        forbidden=("X" * 1024,) * 100,
        limits=replace(_limits(), maximum_memory_bytes=16_384),
    )
    assert not result.passed, "RV_CANARY_MEMORY_UNBOUNDED"

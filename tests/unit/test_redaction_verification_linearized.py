"""Original-byte linearized envelope proof and adversarial synthetic controls.

PYTEST_DONT_REWRITE
"""

from __future__ import annotations

import hashlib
import re
import struct
import zlib
from collections.abc import Callable
from dataclasses import replace
from io import BytesIO

import pikepdf
import pytest

from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.pdf.redaction_reconstruction import RedactionReconstructionLimits
from paperworkshell.pdf.redaction_verification_linearized import _xref, inspect_linearized_envelope
from paperworkshell.pdf.redaction_verification_structure import (
    _Inspection,
    _objects,
    _Rejected,
    inspect_structure,
)
from tests.unit.test_redaction_verification_viewer_resources import _metadata

_CANARY = "PWS-SYNTHETIC-LINEAR-CANARY"


class _Control:
    def __init__(self, *, cancelled: bool = False, expired: bool = False) -> None:
        self.cancelled, self.expired = cancelled, expired

    def cancellation_requested(self) -> bool:
        return self.cancelled

    def deadline_expired(self) -> bool:
        return self.expired


def _limits() -> RedactionReconstructionLimits:
    return RedactionReconstructionLimits.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1)


def _qpdf_candidate(pages: int = 3) -> bytes:
    with pikepdf.Pdf.new() as document:
        for _ in range(pages):
            page = document.add_blank_page(page_size=(72, 48))
            page.Contents = pikepdf.Stream(document, b"q\nQ")
        target = BytesIO()
        document.save(
            target,
            linearize=True,
            deterministic_id=True,
            object_stream_mode=pikepdf.ObjectStreamMode.disable,
        )
    return target.getvalue()


def _synthetic(
    *,
    eol: bytes = b"\n",
    linear_extra: bytes = b"",
    hint_extra: bytes = b"",
    compressed: bool = False,
    transform: Callable[[bytes], bytes] = lambda value: value,
) -> bytes:
    """Small complete two-xref fixture with independently assembled hint tables.

    All variable decimal fields have fixed width, so malformed table controls do
    not accidentally test stale offsets instead of the intended hint failure.
    """

    def number(value: int) -> bytes:
        return f"{value:010d}".encode()

    def indirect(identity: int, body: bytes) -> bytes:
        return str(identity).encode() + b" 0 obj" + eol + body + eol + b"endobj" + eol

    def stream(data: bytes) -> bytes:
        return (
            b"<< /Length "
            + number(len(data))
            + b" >>"
            + eol
            + b"stream"
            + eol
            + data
            + eol
            + b"endstream"
        )

    def page(contents: int) -> bytes:
        return (
            b"<< /Type /Page /Parent 5 0 R /MediaBox [0 0 72 48] /Resources << >> /Contents "
            + str(contents).encode()
            + b" 0 R >>"
        )

    bodies = {
        7: b"<< /Type /Catalog /Pages 5 0 R >>",
        9: page(10),
        10: stream(b"q\nQ"),
        1: page(2),
        2: stream(b"q\nQ"),
        3: page(4),
        4: stream(b"q\nQ"),
        5: b"<< /Type /Pages /Count 3 /Kids [9 0 R 1 0 R 3 0 R] >>",
    }

    def serialize(
        hints: bytes, facts: dict[str, int]
    ) -> tuple[bytes, dict[int, int], dict[str, int]]:
        record_ending = (b" " if len(eol) == 1 else b"") + eol
        output = bytearray(b"%PDF-1.7" + eol + b"%\xbf\xf7\xa2\xfe" + eol)
        offsets: dict[int, int] = {6: len(output)}
        linear = (
            b"<< /Linearized 1 /L "
            + number(facts.get("size", 0))
            + b" /H ["
            + number(facts.get("hint", 0))
            + b" "
            + number(facts.get("hint_length", 0))
            + b"] /O 9 /E "
            + number(facts.get("end", 0))
            + b" /N 3 /T "
            + number(facts.get("zero", 0))
            + linear_extra
            + b" >>"
        )
        output.extend(indirect(6, linear) + b" " * 128 + eol)
        front = len(output)
        output.extend(b"xref" + eol + b"6 5" + eol)
        for identity in range(6, 11):
            output.extend(number(facts.get(str(identity), 0)) + b" 00000 n" + record_ending)
        output.extend(
            b"trailer << /Root 7 0 R /Size 11 /Prev "
            + number(facts.get("back", 0))
            + b" >>"
            + eol
            + b"startxref"
            + eol
            + b"0"
            + eol
            + b"%%EOF"
            + eol
        )
        for identity in [7, 8, 9, 10, 1, 2, 3, 4, 5]:
            offsets[identity] = len(output)
            body = bodies.get(identity, b"")
            if identity == 8:
                raw_hints = zlib.compress(hints) if compressed else hints
                body = (
                    b"<< /S 60 /Length "
                    + number(len(raw_hints))
                    + (b" /Filter /FlateDecode" if compressed else b"")
                    + hint_extra
                    + b" >>"
                    + eol
                    + b"stream"
                    + eol
                    + raw_hints
                    + eol
                    + b"endstream"
                )
            output.extend(indirect(identity, body))
        back = len(output)
        output.extend(b"xref" + eol + b"0 6" + eol)
        zero = len(output) - 1
        output.extend(b"0000000000 65535 f" + record_ending)
        for identity in range(1, 6):
            output.extend(number(offsets[identity]) + b" 00000 n" + record_ending)
        output.extend(
            b"trailer << /Size 6 >>"
            + eol
            + b"startxref"
            + eol
            + str(front).encode()
            + eol
            + b"%%EOF"
            + eol
        )
        return (
            bytes(output),
            offsets,
            {
                "size": len(output),
                "back": back,
                "zero": zero,
                "hint": offsets[8],
                "hint_length": offsets[9] - offsets[8],
                "end": offsets[1],
                **{str(identity): offset for identity, offset in offsets.items()},
            },
        )

    hints = transform(b"\0" * 93)
    _, offsets, facts = serialize(hints, {})
    first_length = offsets[1] - offsets[9]
    second_length = offsets[3] - offsets[1]
    third_length = offsets[5] - offsets[3]
    lengths = struct.pack(">3I", first_length, second_length, third_length)
    header = struct.pack(
        ">IIHIHIHIHHHHH", 2, offsets[9] - facts["hint_length"], 0, 0, 32, 0, 0, 0, 32, 0, 0, 0, 1
    )
    shared = struct.pack(">4IHIH", 0, 0, 2, 2, 0, 0, 32)
    shared += struct.pack(">2I", offsets[10] - offsets[9], offsets[1] - offsets[10]) + b"\0"
    hints = transform(header + lengths + lengths + shared)
    _, _, facts = serialize(hints, facts)
    content, _, _ = serialize(hints, facts)
    return content


def _inspect(
    content: bytes,
    *,
    limits: RedactionReconstructionLimits | None = None,
    control: _Control | None = None,
    tokens: tuple[str, ...] = (_CANARY,),
    hashes: tuple[str, ...] = (),
) -> tuple[str, frozenset[tuple[int, int]], int]:
    source = BytesIO(content)
    source.seek(9)
    inspection = _Inspection(tokens, hashes, limits or _limits(), control or _Control())
    try:
        with pikepdf.Pdf.open(
            BytesIO(content),
            attempt_recovery=False,
            inherit_page_attributes=False,
            suppress_warnings=True,
        ) as document:
            source.seek(9)
            result = inspect_linearized_envelope(source, document, inspection)
        return "RV_OBJECTS_CLEAR", result, inspection.decoded_bytes
    except _Rejected as error:
        return error.code, frozenset(), inspection.decoded_bytes
    except pikepdf.PdfError:
        return "RV_NATIVE_REJECTED", frozenset(), inspection.decoded_bytes
    finally:
        assert source.tell() == 9, "RV_POSITION_CHANGED"
        assert source.getvalue() == content, "RV_SOURCE_CHANGED"


@pytest.mark.parametrize("pages", [1, 2, 3, 16])
def test_real_qpdf_linearized_envelopes_pass_without_rewriting(pages: int) -> None:
    content = _qpdf_candidate(pages)
    result, auxiliary, decoded = _inspect(content)
    assert result == "RV_OBJECTS_CLEAR" and len(auxiliary) == 2 and decoded > 0, (
        "RV_QPDF_ENVELOPE_FAILED"
    )


@pytest.mark.parametrize("eol", [b"\n", b"\r", b"\r\n"])
def test_independent_two_section_fixture_and_all_legal_line_endings(eol: bytes) -> None:
    content = _synthetic(eol=eol)
    result, auxiliary, decoded = _inspect(content)
    assert (
        result == "RV_OBJECTS_CLEAR" and auxiliary == frozenset({(6, 0), (8, 0)}) and decoded == 93
    ), "RV_TWO_SECTION_FAILED"


@pytest.mark.parametrize("suffix", [b"TRAILING", b"\n%%EOF\n", b"\nstartxref\n0\n%%EOF\n"])
def test_additional_tail_bytes_and_revision_markers_fail(suffix: bytes) -> None:
    assert _inspect(_synthetic() + suffix)[0] != "RV_OBJECTS_CLEAR", "RV_HISTORY_ALLOWED"


@pytest.mark.parametrize("field", [b"/L", b"/O", b"/E", b"/N", b"/T"])
def test_linearization_parameters_are_bound_to_original_physical_file(field: bytes) -> None:
    content = _synthetic()
    pattern = re.compile(re.escape(field) + rb" ([0-9]+)")
    content = pattern.sub(lambda match: field + b" " + b"0" * len(match[1]), content, count=1)
    assert _inspect(content)[0] != "RV_OBJECTS_CLEAR", "RV_PARAMETER_NOT_BOUND"


@pytest.mark.parametrize(
    "extra", [b" /Unknown 1", b" /P 1", b" /P 0 /P 0", b" /Prev 1", b" /Info 7 0 R"]
)
def test_linearization_dictionary_is_closed_and_has_no_carriers(extra: bytes) -> None:
    assert _inspect(_synthetic(linear_extra=extra))[0] != "RV_OBJECTS_CLEAR", (
        "RV_LINEAR_CARRIER_ALLOWED"
    )


@pytest.mark.parametrize(
    "extra",
    [
        b" /O 60",
        b" /Unknown 1",
        b" /I 91",
        b" /Length 93",
        b" /Filter /Unknown",
        b" /Payload (UNRECOGNIZED)",
    ],
)
def test_hint_dictionary_is_closed_and_all_auxiliary_offsets_are_checked(extra: bytes) -> None:
    assert _inspect(_synthetic(hint_extra=extra))[0] != "RV_OBJECTS_CLEAR", (
        "RV_HINT_CARRIER_ALLOWED"
    )


def test_unindexed_object_in_front_padding_is_not_treated_as_whitespace() -> None:
    content = _synthetic()
    insertion = b"99 0 obj (UNINDEXED CONTENT) endobj"
    content = content.replace(b" " * 128, insertion.ljust(128), 1)
    assert _inspect(content, tokens=())[0] != "RV_OBJECTS_CLEAR", "RV_UNINDEXED_ALLOWED"


def test_dictionary_comments_are_not_a_hidden_payload_channel() -> None:
    content = _synthetic().replace(b" " * 128, b"%COMMENT\n".ljust(128), 1)
    assert _inspect(content, tokens=())[0] != "RV_OBJECTS_CLEAR", "RV_COMMENT_ALLOWED"


def test_xref_cannot_shadow_another_physical_object() -> None:
    content = _synthetic()
    records = list(re.finditer(rb"[0-9]{10} 00000 n", content))
    at = records[2].start()
    content = content[:at] + records[1][0] + content[records[2].end() :]
    assert _inspect(content)[0] != "RV_OBJECTS_CLEAR", "RV_XREF_SHADOW_ALLOWED"


def test_zero_entry_address_is_bound_even_with_reordered_xref_subsections() -> None:
    content = (
        b"xref\n1 1\n0000000007 00000 n \n0 1\n0000000000 65535 f \n"
        b"trailer << /Size 2 >>\nstartxref\n7\n%%EOF\n"
    )
    inspection = _Inspection((), (), _limits(), _Control())
    entries, trailer, zero = _xref(BytesIO(content), 0, len(content), 7, inspection)
    assert entries == {(1, 0): 7} and trailer.size == 2 and zero == content.index(b"0000000000"), (
        "RV_ZERO_ENTRY_UNBOUND"
    )


@pytest.mark.parametrize(
    "transform",
    [
        lambda value: value + b"\0",
        lambda value: value + b"OPAQUE PAYLOAD",
        lambda value: value[:16],
        lambda value: b"\xff" * len(value),
        lambda value: value[:60] + b"\xff\xff\xff\xff" + value[64:],
        lambda value: value[:92] + b"\x80" + value[93:],
        lambda value: value[:92] + b"\x01" + value[93:],
    ],
)
def test_every_hint_byte_and_alignment_bit_is_parsed(transform: Callable[[bytes], bytes]) -> None:
    assert _inspect(_synthetic(transform=transform), tokens=())[0] != "RV_OBJECTS_CLEAR", (
        "RV_OPAQUE_HINT_ALLOWED"
    )


@pytest.mark.parametrize(
    "offset", [0, 4, 10, 16, 22, 32, 34, 36, 60, 64, 68, 72, 76, 78, 82, 84, 88]
)
def test_hint_numeric_fields_bind_to_bounded_object_spans(offset: int) -> None:
    def corrupt(value: bytes) -> bytes:
        data = bytearray(value)
        data[offset] ^= 128
        return bytes(data)

    assert _inspect(_synthetic(transform=corrupt), tokens=())[0] != "RV_OBJECTS_CLEAR", (
        "RV_HINT_PARAMETER_ALLOWED"
    )


def test_canary_in_raw_auxiliary_bytes_is_scanned_without_leaking_it() -> None:
    content = _synthetic(transform=lambda value: value + _CANARY.encode())
    result = _inspect(content)
    assert result[0] == "RV_CANARY_RECOVERED" and _CANARY not in repr(result), (
        "RV_AUXILIARY_CANARY_MISSED"
    )


def test_exact_original_hint_payload_fingerprint_is_rejected() -> None:
    content = _synthetic()
    match = re.search(rb"/S 60 /Length [0-9]+ >>\nstream\n(.*?)\nendstream", content, re.S)
    assert match is not None, "RV_TEST_HINT_MISSING"
    digest = hashlib.sha256(match[1]).hexdigest()
    assert _inspect(content, hashes=(digest,))[0] == "RV_ORIGINAL_OBJECT_RETAINED", (
        "RV_AUXILIARY_FINGERPRINT_MISSED"
    )


def test_compressed_hint_data_is_independently_decoded_and_scanned() -> None:
    assert _inspect(_synthetic(compressed=True))[0] == "RV_OBJECTS_CLEAR", (
        "RV_COMPRESSED_HINT_FAILED"
    )
    content = _synthetic(compressed=True, transform=lambda value: value + _CANARY.encode())
    assert _CANARY.encode() not in content, "RV_TEST_CANARY_NOT_COMPRESSED"
    result = _inspect(content)
    assert result[0] == "RV_CANARY_RECOVERED" and _CANARY not in repr(result), (
        "RV_DECODED_HINT_CANARY_MISSED"
    )


def test_hint_expansion_cannot_allocate_beyond_the_policy() -> None:
    content = _synthetic(compressed=True, transform=lambda value: value + b"A" * 100_000)
    result = _inspect(content, limits=replace(_limits(), maximum_memory_bytes=64 * 1024))
    assert result[0] == "RV_LIMIT_EXCEEDED", "RV_HINT_EXPANSION_UNBOUNDED"


@pytest.mark.parametrize(
    "budget",
    ["maximum_source_bytes", "maximum_output_bytes", "maximum_pdf_objects", "maximum_memory_bytes"],
)
def test_resource_policy_budgets_are_enforced(budget: str) -> None:
    result = _inspect(_synthetic(), limits=replace(_limits(), **{budget: 1}))
    assert result[0] != "RV_OBJECTS_CLEAR", "RV_LINEAR_BUDGET_IGNORED"


@pytest.mark.parametrize(
    "control,expected",
    [(_Control(cancelled=True), "RV_CANCELLED"), (_Control(expired=True), "RV_TIMED_OUT")],
)
def test_cancellation_and_timeout_are_checked_during_original_byte_proof(
    control: _Control, expected: str
) -> None:
    assert _inspect(_synthetic(), control=control)[0] == expected, "RV_LINEAR_CONTROL_IGNORED"


@pytest.mark.parametrize("bookkeeping", ["info", "metadata"])
@pytest.mark.parametrize("owner", ["page_resources", "catalog_font", "nested_array"])
def test_structure_itself_rejects_bookkeeping_aliases_outside_their_exact_roles(
    bookkeeping: str, owner: str
) -> None:
    with pikepdf.Pdf.new() as document:
        page = document.add_blank_page(page_size=(72, 48))
        _metadata(document)
        auxiliary = document.trailer.Info if bookkeeping == "info" else document.Root.Metadata
        if owner == "page_resources":
            page.Resources.XObject = pikepdf.Dictionary(Alias=auxiliary)
        elif owner == "catalog_font":
            document.Root.Font = auxiliary
        else:
            page.Resources.Font = pikepdf.Dictionary(Alias=pikepdf.Array([auxiliary]))
        output = BytesIO()
        document.save(
            output,
            deterministic_id=True,
            object_stream_mode=pikepdf.ObjectStreamMode.disable,
            fix_metadata_version=False,
        )
    result = inspect_structure(
        BytesIO(output.getvalue()),
        forbidden_tokens=(),
        forbidden_stream_sha256=(),
        limits=_limits(),
        control=_Control(),
        viewer_resave=True,
    )
    assert not result.passed and result.code == "RV_ORIGINAL_OBJECT_RETAINED", (
        "RV_AUXILIARY_ROLE_NOT_BOUND"
    )


@pytest.mark.parametrize("key", ["/Linearized", "/S"])
def test_physical_envelope_auxiliaries_must_remain_unreachable_from_the_graph(key: str) -> None:
    content = _qpdf_candidate()
    inspection = _Inspection((), (), _limits(), _Control())
    with pikepdf.Pdf.open(BytesIO(content)) as document:
        auxiliary = inspect_linearized_envelope(BytesIO(content), document, inspection)
        alias = next(
            item
            for item in document.objects
            if isinstance(item, (pikepdf.Dictionary, pikepdf.Stream)) and key in item
        )
        document.pages[0].Resources.Font = pikepdf.Dictionary(Alias=alias)
        with pytest.raises(_Rejected) as failure:
            _objects(document, inspection, auxiliary=auxiliary, viewer_resave=True, linearized=True)
        assert failure.value.code == "RV_ORIGINAL_OBJECT_RETAINED", (
            "RV_ENVELOPE_AUXILIARY_ALIAS_ALLOWED"
        )

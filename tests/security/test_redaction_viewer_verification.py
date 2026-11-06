"""Viewer compatibility never relaxes source-absence or publication checks.

PYTEST_DONT_REWRITE

All inputs are small, generated synthetic PDFs. No accepted human artifacts are
read or rewritten by this regression corpus.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from uuid import UUID

import pikepdf
import pytest

from paperworkshell.domain.pdf_export import PdfExportError
from paperworkshell.domain.redaction import PERMANENT_REDACTION_ENABLED
from paperworkshell.domain.redaction_verification import (
    VIEWER_VERIFICATION_PROFILE,
    RedactionVerificationReport,
    RedactionVerificationRequest,
    VerificationCode,
    VerificationMethod,
    VerificationStatus,
    validate_verification_report,
    verification_probe_digest,
)
from paperworkshell.pdf.redaction_verification import verify_redaction
from tests.security.test_redaction_verification_corpus import (
    _CASES,
    _FIXTURES,
    _limits,
    _png_pdf,
    _source_stream_probes,
    _verification_request,
)
from tests.unit.test_redaction_verification_structure import _Control
from tests.unit.test_redaction_verification_viewer_resources import _VALUES, _metadata
from tools.redaction_corpus import RedactionCase

_CANARY = "PWS-VIEWER-REMOVED-CONTROL"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _save(document: pikepdf.Pdf, *, linearized: bool) -> bytes:
    output = BytesIO()
    document.save(
        output,
        deterministic_id=True,
        object_stream_mode=pikepdf.ObjectStreamMode.disable,
        linearize=linearized,
        fix_metadata_version=False,
    )
    return output.getvalue()


def _blank(*, linearized: bool = False) -> bytes:
    with pikepdf.Pdf.new() as document:
        for _ in range(2):
            page = document.add_blank_page(page_size=(80, 60))
            page.Contents = pikepdf.Stream(document, b"q Q")
        return _save(document, linearized=linearized)


def _request(candidate: bytes, reference: bytes) -> RedactionVerificationRequest:
    return RedactionVerificationRequest(
        _digest(candidate),
        len(candidate),
        _digest(reference),
        len(reference),
        2,
        (),
        _digest(b"PWS-0606 synthetic viewer compatibility intent"),
        probes_sha256=verification_probe_digest((_CANARY,), ()),
        profile_id=VIEWER_VERIFICATION_PROFILE,
    )


def _verify(candidate: bytes, reference: bytes, scratch: Path) -> RedactionVerificationReport:
    before = tuple(scratch.iterdir())
    source, oracle = BytesIO(candidate), BytesIO(reference)
    source.seek(7)
    oracle.seek(11)
    report = verify_redaction(
        source,
        oracle,
        _request(candidate, reference),
        scratch,
        _limits(),
        _Control(),
        forbidden_tokens=(_CANARY,),
    )
    assert source.tell() == 7 and oracle.tell() == 11, "VIEWER_INPUT_POSITION_CHANGED"
    assert source.getvalue() == candidate and oracle.getvalue() == reference
    assert tuple(scratch.iterdir()) == before, "VIEWER_PRIVATE_TEMP_RETAINED"
    assert _CANARY not in repr(report) and _CANARY not in str(report.to_record())
    assert PERMANENT_REDACTION_ENABLED is True, "VIEWER_RELEASE_GATE_ENABLED"
    return report


def test_viewer_linearization_passes_all_automatic_methods_without_release_authority(
    tmp_path: Path,
) -> None:
    reference = _blank()
    with pikepdf.Pdf.open(BytesIO(reference)) as document:
        candidate = _save(document, linearized=True)
    report = _verify(candidate, reference, tmp_path)
    assert report.automated_passed, "VIEWER_LINEARIZATION_NOT_SUPPORTED"
    assert not report.release_ready, "VIEWER_MISSING_GATE_METHOD_ACCEPTED"
    assert {
        item.method for item in report.methods if item.status is VerificationStatus.UNKNOWN
    } == {VerificationMethod.OCR, VerificationMethod.VIEWERS}
    canonical = verify_redaction(
        BytesIO(candidate),
        BytesIO(reference),
        replace(_request(candidate, reference), profile_id="redaction-verification-v1"),
        tmp_path,
        _limits(),
        _Control(),
        forbidden_tokens=(_CANARY,),
    )
    assert not canonical.automated_passed, "VIEWER_WEAKENED_CANONICAL_EXPORT_PROFILE"
    assert (
        next(
            item for item in canonical.methods if item.method is VerificationMethod.STRUCTURE
        ).status
        is VerificationStatus.FAIL
    )
    assert not list(tmp_path.iterdir()), "VIEWER_STRICT_PROFILE_TEMP_RETAINED"


def test_viewer_report_cannot_be_rebound_to_the_canonical_export_profile(tmp_path: Path) -> None:
    reference = _blank()
    report = _verify(reference, reference, tmp_path)
    canonical = replace(_request(reference, reference), profile_id="redaction-verification-v1")
    assert canonical.fingerprint != report.request_sha256, "VIEWER_PROFILE_NOT_BOUND"
    with pytest.raises(PdfExportError):
        validate_verification_report(canonical, report)


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.fixture_id)
def test_viewer_profile_rejects_every_untreated_adversarial_source(
    tmp_path: Path, case: RedactionCase
) -> None:
    # Preserve the complete earlier source-control coverage under the new profile.
    # The annotation/form/metadata/history controls contain valid but unsafe PDFs.
    original = _FIXTURES[case.fixture_id]
    source = _png_pdf(original, tmp_path) if case.fixture_id.startswith("png-") else original
    tokens = tuple(canary.token for canary in case.canaries)
    streams = _source_stream_probes(source, case) if case.expected_open == "accept" else ()
    request = replace(
        _verification_request(
            source,
            source,
            tuple(sorted({region.page_index for region in case.target_regions})),
            4 if case.fixture_id == "pdf-redaction-geometry" else 1,
            tokens,
            streams,
        ),
        profile_id=VIEWER_VERIFICATION_PROFILE,
    )
    report = verify_redaction(
        BytesIO(source),
        BytesIO(source),
        request,
        tmp_path,
        _limits(),
        _Control(),
        forbidden_tokens=tokens,
        forbidden_stream_sha256=streams,
    )
    assert not report.automated_passed, "VIEWER_UNSAFE_SOURCE_ACCEPTED"
    assert not report.release_ready, "VIEWER_UNSAFE_RELEASE_ACCEPTED"
    assert any(item.status is VerificationStatus.FAIL for item in report.methods)
    assert all(canary.token not in str(report.to_record()) for canary in case.canaries)
    assert _FIXTURES[case.fixture_id] == original, "VIEWER_CORPUS_SOURCE_MUTATED"
    assert not list(tmp_path.iterdir()), "VIEWER_CORPUS_TEMP_RETAINED"


@pytest.mark.parametrize("linearized", [False, True])
def test_viewer_profile_cannot_hide_an_unused_image_without_a_known_canary(
    tmp_path: Path, linearized: bool
) -> None:
    reference = _blank()
    with pikepdf.Pdf.open(BytesIO(reference)) as document:
        unused = pikepdf.Stream(
            document,
            b"\x00\x7f\xff\x00",
            Type=pikepdf.Name.XObject,
            Subtype=pikepdf.Name.Image,
            Width=2,
            Height=2,
            ColorSpace=pikepdf.Name.DeviceGray,
            BitsPerComponent=8,
        )
        document.pages[0].Resources.XObject = pikepdf.Dictionary(Unused=unused)
        candidate = _save(document, linearized=linearized)
    report = _verify(candidate, reference, tmp_path)
    assert not report.automated_passed, "VIEWER_UNUSED_IMAGE_ACCEPTED"
    assert (
        next(item for item in report.methods if item.method is VerificationMethod.STRUCTURE).status
        is VerificationStatus.FAIL
    )


def _append_revision(content: bytes) -> bytes:
    """Append a valid, semantically unchanged catalog revision, without a canary."""
    with pikepdf.Pdf.open(BytesIO(content), attempt_recovery=False) as document:
        root_number, root_generation = document.Root.objgen
        root = document.Root.unparse(resolved=True)
        size = int(document.trailer.Size)
    prior = re.findall(rb"startxref\s+([0-9]+)\s+%%EOF", content)
    assert prior, "VIEWER_REVISION_CONTROL_INVALID"
    output = bytearray(content)
    offset = len(output)
    output.extend(f"{root_number} {root_generation} obj\n".encode() + root + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n{root_number} 1\n{offset:010d} {root_generation:05d} n \n".encode())
    output.extend(
        f"trailer\n<< /Root {root_number} {root_generation} R /Size {size} /Prev ".encode()
        + prior[-1]
        + b" >>\nstartxref\n"
        + str(xref).encode()
        + b"\n%%EOF\n"
    )
    return bytes(output)


@pytest.mark.parametrize("linearized", [False, True])
def test_viewer_profile_rejects_genuine_incremental_history_even_without_a_canary(
    tmp_path: Path, linearized: bool
) -> None:
    reference = _blank()
    with pikepdf.Pdf.open(BytesIO(reference)) as document:
        prior = _save(document, linearized=linearized)
    candidate = _append_revision(prior)
    # Native parsing sees the unchanged visible document; raw historical bytes
    # must still be rejected independently of rendering and known-token probes.
    with pikepdf.Pdf.open(BytesIO(candidate), attempt_recovery=False) as document:
        assert len(document.pages) == 2, "VIEWER_REVISION_CONTROL_UNREADABLE"
    report = _verify(candidate, reference, tmp_path)
    assert not report.automated_passed, "VIEWER_INCREMENTAL_HISTORY_ACCEPTED"
    assert (
        next(item for item in report.methods if item.method is VerificationMethod.STRUCTURE).status
        is VerificationStatus.FAIL
    )


@pytest.mark.parametrize("linearized", [False, True])
@pytest.mark.parametrize("carrier", ["catalog", "page", "info", "unpainted_content"])
def test_viewer_profile_keeps_unrecognized_content_carriers_closed(
    tmp_path: Path, linearized: bool, carrier: str
) -> None:
    reference = _blank()
    with pikepdf.Pdf.open(BytesIO(reference)) as document:
        value = pikepdf.String("SYNTHETIC UNAUTHENTICATED VALUE")
        if carrier == "catalog":
            document.Root.PieceInfo = pikepdf.Dictionary(Vendor=value)
        elif carrier == "page":
            document.pages[0].ActualText = value
        elif carrier == "info":
            document.docinfo.Title = value
        else:
            document.pages[0].Contents = pikepdf.Stream(
                document, b"q Q /Hidden (SYNTHETIC UNAUTHENTICATED VALUE)"
            )
        candidate = _save(document, linearized=linearized)
    report = _verify(candidate, reference, tmp_path)
    assert not report.automated_passed, "VIEWER_UNAUTHENTICATED_CARRIER_ACCEPTED"


@pytest.mark.parametrize("linearized", [False, True])
def test_known_removed_text_cannot_be_split_across_typed_metadata_identifiers(
    tmp_path: Path, linearized: bool
) -> None:
    token = "PWSREDTXTALPHA"
    left, right = bytearray(16), bytearray(16)
    left[6], left[8], right[8] = 0x40, 0x80, 0x80
    left[9:] = token[:7].encode()
    right[:7] = token[7:].encode()
    assert token.encode() not in left and token.encode() not in right
    assert token.encode() in left + right, "VIEWER_UUID_CONTROL_NOT_RECOVERABLE"
    values = dict(_VALUES)
    values["DocumentID"] = "uuid:" + str(UUID(bytes=bytes(left)))
    values["InstanceID"] = "uuid:" + str(UUID(bytes=bytes(right)))
    reference = _blank()
    with pikepdf.Pdf.open(BytesIO(reference)) as document:
        _metadata(document, values)
        candidate = _save(document, linearized=linearized)
    report = verify_redaction(
        BytesIO(candidate),
        BytesIO(reference),
        replace(
            _request(candidate, reference), probes_sha256=verification_probe_digest((token,), ())
        ),
        tmp_path,
        _limits(),
        _Control(),
        forbidden_tokens=(token,),
    )
    assert not report.automated_passed, "VIEWER_SPLIT_METADATA_CANARY_ACCEPTED"
    assert (
        next(item for item in report.methods if item.method is VerificationMethod.STRUCTURE).code
        is VerificationCode.CANARY
    )
    assert token not in str(report.to_record()), "VIEWER_SPLIT_METADATA_CANARY_LOGGED"
    assert not list(tmp_path.iterdir()), "VIEWER_SPLIT_METADATA_TEMP_RETAINED"


@pytest.mark.parametrize("role", ["info", "metadata", "hints", "linearization"])
def test_validated_bookkeeping_cannot_be_aliased_as_an_extra_page_resource(
    tmp_path: Path, role: str
) -> None:
    reference = _blank()
    with pikepdf.Pdf.open(BytesIO(_blank(linearized=True))) as document:
        _metadata(document)
        if role == "info":
            auxiliary = document.trailer.Info
        elif role == "metadata":
            auxiliary = document.Root.Metadata
        else:
            key = "/S" if role == "hints" else "/Linearized"
            auxiliary = next(
                item
                for item in document.objects
                if isinstance(item, (pikepdf.Dictionary, pikepdf.Stream)) and key in item
            )
        document.pages[0].Resources.XObject = pikepdf.Dictionary(AuxiliaryAlias=auxiliary)
        candidate = _save(document, linearized=True)
    report = _verify(candidate, reference, tmp_path)
    assert not report.automated_passed, "VIEWER_AUXILIARY_ALIAS_ACCEPTED"
    structure = next(item for item in report.methods if item.method is VerificationMethod.STRUCTURE)
    assert structure.status is VerificationStatus.FAIL
    if role in {"info", "metadata"}:
        assert structure.code is VerificationCode.ORIGINAL_OBJECT, "VIEWER_ALIAS_BINDING_NOT_RUN"


_ID_TOKEN = "PWSREDTXTALPHA"
_ID_PROFILES = ("redaction-verification-v1", VIEWER_VERIFICATION_PROFILE)


def _identifier_pair(token: str, encoding: str) -> tuple[bytes, bytes]:
    first, second = token[:7], token[7:]
    raw = (b"\0" * 9 + first.encode(), second.encode() + b"\0" * 9)
    if encoding == "bytes":
        return raw
    codec = "utf-16-le" if encoding.endswith("le") else "utf-16-be"
    bom = b"\xff\xfe" if codec.endswith("le") else b"\xfe\xff"
    wide = (bom + first.encode(codec), bom + second.encode(codec))
    return (raw[0], wide[1]) if encoding.startswith("mixed") else wide


def _replace_identifier_pair(content: bytes, pair: tuple[bytes, bytes]) -> bytes:
    assert all(len(value) == 16 for value in pair), "ID_CONTROL_SIZE_INVALID"
    pattern = rb"/ID\s*\[\s*<([0-9a-fA-F]{32})>\s*<([0-9a-fA-F]{32})>\s*\]"

    def patch(match: re.Match[bytes]) -> bytes:
        data = match[0]
        first_at, second_at = match.start(1) - match.start(), match.start(2) - match.start()
        return (
            data[:first_at]
            + pair[0].hex().encode()
            + data[first_at + 32 : second_at]
            + pair[1].hex().encode()
            + data[second_at + 32 :]
        )

    candidate, count = re.subn(pattern, patch, content)
    assert count in {1, 2} and len(candidate) == len(content), "ID_CONTROL_ENVELOPE_CHANGED"
    return candidate


def _verify_identifiers(
    candidate: bytes,
    reference: bytes,
    scratch: Path,
    profile: str,
    *,
    tokens: tuple[str, ...] = (_ID_TOKEN,),
    hashes: tuple[str, ...] = (),
) -> RedactionVerificationReport:
    report = verify_redaction(
        BytesIO(candidate),
        BytesIO(reference),
        replace(
            _request(candidate, reference),
            profile_id=profile,
            probes_sha256=verification_probe_digest(tokens, hashes),
        ),
        scratch,
        _limits(),
        _Control(),
        forbidden_tokens=tokens,
        forbidden_stream_sha256=hashes,
    )
    assert not list(scratch.iterdir()), "ID_VERIFICATION_TEMP_RETAINED"
    assert all(token not in str(report.to_record()) for token in tokens), "ID_VALUE_LEAKED"
    assert PERMANENT_REDACTION_ENABLED is True
    return report


@pytest.mark.parametrize("profile", _ID_PROFILES)
@pytest.mark.parametrize("encoding", ["bytes", "utf16be", "utf16le", "mixedbe", "mixedle"])
@pytest.mark.parametrize("reverse", [False, True])
def test_removed_value_split_across_trailer_identifiers_fails_every_supported_profile(
    tmp_path: Path, profile: str, encoding: str, reverse: bool
) -> None:
    reference = _blank()
    pair = _identifier_pair(_ID_TOKEN, encoding)
    if reverse:
        pair = pair[::-1]
    candidate = _replace_identifier_pair(reference, pair)
    with pikepdf.Pdf.open(BytesIO(candidate), attempt_recovery=False) as document:
        views = [(bytes(value), str(value).encode()) for value in document.trailer.ID]
        assert all(_ID_TOKEN.encode() not in value for group in views for value in group)
        assert any(
            _ID_TOKEN.encode() in left + right
            for first, second in ((views[0], views[1]), (views[1], views[0]))
            for left in first
            for right in second
        ), "ID_CONTROL_NOT_RECOVERABLE"
        assert not document.get_warnings(), "ID_CONTROL_NATIVE_WARNING"
    report = _verify_identifiers(candidate, reference, tmp_path, profile)
    assert not report.automated_passed, "ID_SPLIT_CANARY_ACCEPTED"
    assert report.methods[0].code is VerificationCode.CANARY, "ID_SPLIT_SCAN_NOT_RUN"


@pytest.mark.parametrize("encoding", ["bytes", "mixedle"])
@pytest.mark.parametrize("reverse", [False, True])
def test_linearized_viewer_identifier_pair_is_not_exempt_from_canary_scanning(
    tmp_path: Path, encoding: str, reverse: bool
) -> None:
    reference = _blank()
    pair = _identifier_pair(_ID_TOKEN, encoding)
    candidate = _replace_identifier_pair(_blank(linearized=True), pair[::-1] if reverse else pair)
    report = _verify_identifiers(candidate, reference, tmp_path, VIEWER_VERIFICATION_PROFILE)
    assert not report.automated_passed, "ID_LINEARIZED_SPLIT_CANARY_ACCEPTED"
    assert report.methods[0].code is VerificationCode.CANARY, "ID_LINEARIZED_SCAN_NOT_RUN"


@pytest.mark.parametrize("profile", _ID_PROFILES)
@pytest.mark.parametrize("encoding", ["bytes", "utf16be", "mixedle"])
def test_benign_trailer_identifier_views_still_pass(
    tmp_path: Path, profile: str, encoding: str
) -> None:
    reference = _blank()
    candidate = _replace_identifier_pair(reference, _identifier_pair("PWSKEEPCONTROL", encoding))
    assert _verify_identifiers(candidate, reference, tmp_path, profile).automated_passed


def test_benign_linearized_identifier_views_still_pass(tmp_path: Path) -> None:
    reference = _blank()
    candidate = _replace_identifier_pair(
        _blank(linearized=True), _identifier_pair("PWSKEEPCONTROL", "mixedbe")
    )
    assert _verify_identifiers(
        candidate, reference, tmp_path, VIEWER_VERIFICATION_PROFILE
    ).automated_passed


@pytest.mark.parametrize("profile", _ID_PROFILES)
@pytest.mark.parametrize(
    "view", ["raw_individual", "decoded_individual", "raw_pair", "decoded_pair", "mixed_pair"]
)
def test_original_payload_fingerprints_cannot_move_into_trailer_identifiers(
    tmp_path: Path, profile: str, view: str
) -> None:
    reference = _blank()
    encoding = "mixedbe" if view == "mixed_pair" else "utf16be"
    pair = _identifier_pair(_ID_TOKEN, encoding)
    candidate = _replace_identifier_pair(reference, pair)
    with pikepdf.Pdf.open(BytesIO(candidate), attempt_recovery=False) as document:
        raw = [bytes(value) for value in document.trailer.ID]
        decoded = [str(value).encode() for value in document.trailer.ID]
    payload = {
        "raw_individual": raw[0],
        "decoded_individual": decoded[0],
        "raw_pair": raw[0] + raw[1],
        "decoded_pair": decoded[0] + decoded[1],
        "mixed_pair": raw[0] + decoded[1],
    }[view]
    report = _verify_identifiers(
        candidate, reference, tmp_path, profile, tokens=(), hashes=(_digest(payload),)
    )
    assert not report.automated_passed, "ID_ORIGINAL_PAYLOAD_ACCEPTED"
    assert report.methods[0].code is VerificationCode.ORIGINAL_OBJECT, "ID_FINGERPRINT_SCAN_NOT_RUN"

"""Direct strict runtime-profile hostile checks, with no native OCR claim.

The explicitly test-only OCR runner returns a pass to prove that external method
success cannot override an unsafe structural/text/image/render/resave result.
No viewer runner or viewer testimony is supplied.
"""

from __future__ import annotations

from dataclasses import replace
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

import pikepdf
import pytest

from paperworkshell.domain.redaction_verification import (
    AUTOMATED_METHODS,
    RUNTIME_VERIFICATION_PROFILE,
    RedactionVerificationReport,
    VerificationCode,
    VerificationMethod,
    VerificationMethodResult,
    VerificationStatus,
    verification_probe_digest,
)
from paperworkshell.pdf.redaction_verification import verify_redaction
from tests.security.test_redaction_reconstruction import _Control
from tests.security.test_redaction_verification_corpus import (
    _CASES,
    _FIXTURES,
    _limits,
    _png_pdf,
    _prepare,
    _source_stream_probes,
    _verification_request,
)
from tests.security.test_redaction_viewer_verification import (
    _ID_TOKEN,
    _blank,
    _digest,
    _identifier_pair,
    _replace_identifier_pair,
    _request,
)
from tools.redaction_corpus import RedactionCase


def _test_only_ocr_pass(_source: BinaryIO) -> VerificationMethodResult:
    """A deliberate external-pass control; this does not execute real OCR."""
    return VerificationMethodResult(
        VerificationMethod.OCR, VerificationStatus.PASS, VerificationCode.CLEAR
    )


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.fixture_id)
def test_runtime_profile_security_probe_rejects_every_untreated_source(
    tmp_path: Path, case: RedactionCase
) -> None:
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
        profile_id=RUNTIME_VERIFICATION_PROFILE,
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
        ocr_runner=_test_only_ocr_pass,
    )
    assert any(
        item.method in AUTOMATED_METHODS and item.status is VerificationStatus.FAIL
        for item in report.methods
    ), "RUNTIME_UNSAFE_AUTOMATED_FAILURE_MISSING"
    assert not report.automated_passed and not report.runtime_ready and not report.release_ready
    assert all(token not in repr(report) + str(report.to_record()) for token in tokens)
    assert _FIXTURES[case.fixture_id] == original
    assert not list(tmp_path.iterdir())


def _runtime_identifier_probe(
    candidate: bytes,
    reference: bytes,
    scratch: Path,
    *,
    tokens: tuple[str, ...] = (_ID_TOKEN,),
    hashes: tuple[str, ...] = (),
) -> RedactionVerificationReport:
    return verify_redaction(
        BytesIO(candidate),
        BytesIO(reference),
        replace(
            _request(candidate, reference),
            profile_id=RUNTIME_VERIFICATION_PROFILE,
            probes_sha256=verification_probe_digest(tokens, hashes),
        ),
        scratch,
        _limits(),
        _Control(),
        forbidden_tokens=tokens,
        forbidden_stream_sha256=hashes,
        ocr_runner=_test_only_ocr_pass,
    )


@pytest.mark.parametrize("encoding", ["bytes", "utf16be", "utf16le", "mixedbe", "mixedle"])
@pytest.mark.parametrize("reverse", [False, True])
def test_runtime_profile_security_probe_split_trailer_identifiers(
    tmp_path: Path, encoding: str, reverse: bool
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
        ), "RUNTIME_ID_CONTROL_NOT_RECOVERABLE"
        assert not document.get_warnings()
    report = _runtime_identifier_probe(candidate, reference, tmp_path)
    assert report.methods[0].code is VerificationCode.CANARY
    assert not report.automated_passed and not report.runtime_ready and not report.release_ready
    assert _ID_TOKEN not in repr(report) + str(report.to_record())
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "view", ["raw_individual", "decoded_individual", "raw_pair", "decoded_pair", "mixed_pair"]
)
def test_runtime_profile_security_probe_original_payload_in_identifiers(
    tmp_path: Path, view: str
) -> None:
    reference = _blank()
    pair = _identifier_pair(_ID_TOKEN, "mixedbe" if view == "mixed_pair" else "utf16be")
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
    report = _runtime_identifier_probe(
        candidate, reference, tmp_path, tokens=(), hashes=(_digest(payload),)
    )
    assert report.methods[0].code is VerificationCode.ORIGINAL_OBJECT
    assert not report.automated_passed and not report.runtime_ready and not report.release_ready
    assert not list(tmp_path.iterdir())


def test_runtime_profile_security_probe_safe_output_still_requires_actual_ocr(
    tmp_path: Path,
) -> None:
    case = next(value for value in _CASES if value.fixture_id == "pdf-redaction-born-digital")
    prepared = _prepare(case, tmp_path)
    tokens = tuple(canary.token for canary in case.canaries)
    request = replace(
        _verification_request(
            prepared.candidate,
            prepared.reference,
            prepared.affected_pages,
            1,
            tokens,
            prepared.forbidden_streams,
        ),
        profile_id=RUNTIME_VERIFICATION_PROFILE,
    )
    report = verify_redaction(
        BytesIO(prepared.candidate),
        BytesIO(prepared.reference),
        request,
        tmp_path,
        _limits(),
        _Control(),
        forbidden_tokens=tokens,
        forbidden_stream_sha256=prepared.forbidden_streams,
    )
    assert report.automated_passed
    assert {
        item.method for item in report.methods if item.status is VerificationStatus.UNKNOWN
    } == {VerificationMethod.OCR, VerificationMethod.VIEWERS}
    assert not report.runtime_ready and not report.release_ready
    assert all(token not in repr(report) + str(report.to_record()) for token in tokens)
    assert not list(tmp_path.iterdir())

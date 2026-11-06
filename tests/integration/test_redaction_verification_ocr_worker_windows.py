"""Verify the exact native OCR and sanitizer outputs against their trusted reference.

PYTEST_DONT_REWRITE

The reused worker scenario owns admitted runtime setup, constrained resource
checks and immutable-source assertions. This adds independent verifier coverage
of actual regenerated font CMaps, invisible glyph geometry and masked pixels.
It supplies neither independent OCR evidence nor human viewer approval.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from io import BytesIO
from pathlib import Path

import pikepdf
import pytest

from paperworkshell.domain.redaction import PERMANENT_REDACTION_ENABLED
from paperworkshell.domain.redaction_verification import (
    RedactionVerificationRequest,
    VerificationMethod,
    VerificationStatus,
    verification_probe_digest,
)
from paperworkshell.pdf.redaction_verification import verify_redaction
from tests.integration import test_redaction_sanitization_worker_windows as native_sanitization
from tests.integration.test_redaction_ocr_worker_windows import (
    _SCENARIOS,
    _assert_search,
    _Case,
    _payload,
    _request,
)
from tests.integration.test_redaction_ocr_worker_windows import _case as _create_case
from tests.security.test_redaction_reconstruction import _Control, _limits
from tests.worker_resources import WorkerTestResources

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="requires the supported Windows AppContainer OCR worker"
)


def test_native_ocr_sanitizer_outputs_pass_independent_verification(
    tmp_path: Path,
    worker_test_resources: WorkerTestResources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads: list[bytes] = []
    cases: list[_Case] = []

    def capture_payload(read: Callable[[int], bytes]) -> bytes:
        content = _payload(read)
        payloads.append(content)
        return content

    def capture_case(workspace: Path, original: bytes) -> _Case:
        case = _create_case(workspace, original)
        cases.append(case)
        return case

    with monkeypatch.context() as patch:
        patch.setattr(native_sanitization, "_payload", capture_payload)
        patch.setattr(native_sanitization, "_case", capture_case)
        native_sanitization.test_native_sanitization_preserves_real_regenerated_ocr_and_masked_image(
            tmp_path, worker_test_resources
        )
    assert len(payloads) == 2 and len(cases) == 2, "NATIVE_STAGE_CAPTURE_INVALID"
    recognized, final = payloads
    original_case = cases[0]
    ocr_request = _request(original_case, _SCENARIOS[0])
    with pikepdf.Pdf.open(BytesIO(original_case.original), attempt_recovery=False) as original:
        contents = original.pages[0].Contents
        assert isinstance(contents, pikepdf.Stream), "NATIVE_SOURCE_STREAM_UNSUPPORTED"
        source_streams = (hashlib.sha256(contents.read_bytes()).hexdigest(),)
    tokens = ("SECRET", "PWS-SYNTHETIC-SANITIZER-METADATA-CANARY")
    request = RedactionVerificationRequest(
        candidate_sha256=hashlib.sha256(final).hexdigest(),
        candidate_byte_size=len(final),
        reference_sha256=hashlib.sha256(recognized).hexdigest(),
        reference_byte_size=len(recognized),
        page_count=1,
        affected_pages=(0,),
        intent_sha256=ocr_request.fingerprint,
        probes_sha256=verification_probe_digest(tokens, source_streams),
    )
    scratch = tmp_path / "verification"
    scratch.mkdir()
    candidate, reference = BytesIO(final), BytesIO(recognized)
    candidate.seek(13)
    reference.seek(19)
    report = verify_redaction(
        candidate,
        reference,
        request,
        scratch,
        _limits(),
        _Control(),
        forbidden_tokens=tokens,
        forbidden_stream_sha256=source_streams,
    )
    assert report.automated_passed, "NATIVE_OCR_VERIFICATION_FAILED"
    assert not report.release_ready and report.unsigned_derivative
    for method in (VerificationMethod.OCR, VerificationMethod.VIEWERS):
        assert (
            next(item for item in report.methods if item.method is method).status
            is VerificationStatus.UNKNOWN
        )
    assert candidate.tell() == 13 and reference.tell() == 19
    assert candidate.getvalue() == final and reference.getvalue() == recognized
    assert not list(scratch.iterdir()), "NATIVE_VERIFICATION_TEMP_RETAINED"
    _assert_search(final, "PUBLIC", present=True)
    _assert_search(final, "SECRET", present=False)
    for case in cases:
        case.assert_unchanged()
    assert PERMANENT_REDACTION_ENABLED is True

"""The exact original-to-verifier chain blocks missing evidence and preserves sources."""

from __future__ import annotations

import hashlib
import os
from dataclasses import replace
from io import BytesIO
from pathlib import Path

import pytest

from paperworkshell.domain.pdf_export import PdfExportError
from paperworkshell.domain.redaction_verification import VerificationMethod, VerificationStatus
from paperworkshell.pdf.redaction_export import RedactionExportAdapter
from tests.security.test_redaction_reconstruction import _FIXTURES, _Control, _limits, _request
from tests.unit.test_redaction_reconstruction_models import _id


@pytest.mark.parametrize(
    "fixture_id",
    ["pdf-redaction-born-digital", "pdf-redaction-scan", "pdf-redaction-embedded-image"],
)
def test_real_reconstructed_sanitized_candidate_has_bound_but_incomplete_evidence(
    tmp_path: Path,
    fixture_id: str,
) -> None:
    fixture = _FIXTURES[fixture_id]
    source = fixture.content
    request = _request(fixture_id)
    output = tmp_path / "candidate.pdf"
    report = RedactionExportAdapter().prepare(
        BytesIO(source),
        output,
        tmp_path,
        request,
        _limits(),
        _Control(),
        operation_id=_id(30),
        output_object_id=_id(31),
    )
    assert report.verification.automated_passed
    assert not report.verification.release_ready
    assert [(method.method, method.status) for method in report.verification.methods[-2:]] == [
        (VerificationMethod.OCR, VerificationStatus.UNKNOWN),
        (VerificationMethod.VIEWERS, VerificationStatus.UNKNOWN),
    ]
    assert report.reconstruction.request_sha256 == request.fingerprint
    assert report.verification_request.reference_sha256 == report.reconstruction.output_sha256
    assert report.output_sha256 == hashlib.sha256(output.read_bytes()).hexdigest()
    assert source == fixture.content
    assert list(tmp_path.iterdir()) == [output]
    output.unlink()


def test_changed_source_and_tiny_limits_leave_no_candidate(tmp_path: Path) -> None:
    source = _FIXTURES["pdf-redaction-born-digital"].content
    request = _request("pdf-redaction-born-digital")
    for selected, limits in (
        (source + b"changed", _limits()),
        (source, replace(_limits(), maximum_output_bytes=1)),
        (source, replace(_limits(), maximum_temporary_storage_bytes=len(source) + 1)),
    ):
        with pytest.raises(PdfExportError):
            RedactionExportAdapter().prepare(
                BytesIO(selected),
                tmp_path / "candidate.pdf",
                tmp_path,
                request,
                limits,
                _Control(),
                operation_id=_id(30),
                output_object_id=_id(31),
            )
        assert not list(tmp_path.iterdir())


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows directory sharing")
def test_real_native_scratch_pin_denies_rename_and_expires(tmp_path: Path) -> None:
    from paperworkshell.worker.redaction_export_scratch import PinnedVerificationScratch

    with PinnedVerificationScratch(tmp_path) as scratch:
        directory = scratch.path
        with pytest.raises(PermissionError):
            directory.rename(tmp_path / "substituted")
        scratch.authority.require_directory(directory)
        assert directory.is_dir() and not (tmp_path / "substituted").exists()
    assert not directory.exists()
    with pytest.raises(PdfExportError):
        scratch.authority.require_directory(directory)
    assert not list(tmp_path.iterdir())

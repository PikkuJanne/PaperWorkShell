"""Closed OCR evidence cannot turn removal intent into a verified export."""

from __future__ import annotations

from dataclasses import replace

import pytest

from paperworkshell.domain.ocr import TRUSTED_OCRMYPDF_VERSION, TRUSTED_TESSERACT_VERSION
from paperworkshell.domain.ocr_languages import GERMAN_OCR_LANGUAGE_SET_ID
from paperworkshell.domain.pdf_export import PdfExportError
from paperworkshell.domain.redaction import PERMANENT_REDACTION_ENABLED
from paperworkshell.domain.redaction_ocr import (
    RedactionOcrReport,
    RedactionOcrRequest,
    validate_redaction_ocr_report,
)
from tests.unit.test_redaction_reconstruction_models import report, request


def _report(value: RedactionOcrRequest) -> RedactionOcrReport:
    return RedactionOcrReport(
        request_sha256=value.fingerprint,
        reconstruction=report(value.reconstruction),
        output_sha256="c" * 64,
        output_byte_size=500,
        language_set_id=value.language_set_id,
        ocrmypdf_version=TRUSTED_OCRMYPDF_VERSION,
        tesseract_version=TRUSTED_TESSERACT_VERSION,
    )


def test_current_roundtrip_and_legacy_reconstruction_are_independent() -> None:
    value = RedactionOcrRequest(request())
    result = _report(value)
    assert RedactionOcrRequest.from_record(value.to_record()) == value
    assert RedactionOcrReport.from_record(result.to_record()) == result
    validate_redaction_ocr_report(value, result)
    with pytest.raises(PdfExportError):
        RedactionOcrRequest.from_record(value.reconstruction.to_record())
    with pytest.raises(PdfExportError):
        RedactionOcrReport.from_record(result.reconstruction.to_record())
    assert PERMANENT_REDACTION_ENABLED is True


@pytest.mark.parametrize("version", [0, 2, True, "1"])
def test_unsupported_versions_fail_without_changing_records(version: object) -> None:
    value = RedactionOcrRequest(request())
    records = (value.to_record(), _report(value).to_record())
    for record, decode, member in zip(
        records,
        (RedactionOcrRequest.from_record, RedactionOcrReport.from_record),
        ("request_version", "report_version"),
        strict=True,
    ):
        invalid = {**record, member: version}
        before = dict(invalid)
        with pytest.raises(PdfExportError):
            decode(invalid)
        assert before == invalid


@pytest.mark.parametrize("member", ["path", "script", "verified", "canary", "fallback"])
def test_unknown_members_and_executable_policy_are_rejected(member: str) -> None:
    value = RedactionOcrRequest(request())
    with pytest.raises(PdfExportError):
        RedactionOcrRequest.from_record({**value.to_record(), member: "sensitive-token"})
    with pytest.raises(PdfExportError):
        RedactionOcrReport.from_record({**_report(value).to_record(), member: "sensitive-token"})


@pytest.mark.parametrize("profile", ["searchable-pdf-v1", "", None, True])
def test_only_redacted_page_profile_is_accepted(profile: object) -> None:
    value = RedactionOcrRequest(request())
    with pytest.raises(PdfExportError):
        RedactionOcrRequest.from_record({**value.to_record(), "profile_id": profile})
    with pytest.raises(PdfExportError):
        RedactionOcrReport.from_record({**_report(value).to_record(), "profile_id": profile})


@pytest.mark.parametrize("language", ["eng", "-l eng", "unknown-v1", "../deu", None, True])
def test_only_closed_language_selections_are_accepted(language: object) -> None:
    value = RedactionOcrRequest(request())
    with pytest.raises(PdfExportError):
        RedactionOcrRequest.from_record({**value.to_record(), "language_set_id": language})
    with pytest.raises(PdfExportError):
        RedactionOcrReport.from_record({**_report(value).to_record(), "language_set_id": language})


def test_request_fingerprint_binds_reconstruction_and_language() -> None:
    value = RedactionOcrRequest(request())
    result = _report(value)
    for changed in (
        replace(value, language_set_id=GERMAN_OCR_LANGUAGE_SET_ID),
        replace(value, reconstruction=replace(value.reconstruction, manifest_revision=4)),
    ):
        assert changed.fingerprint != value.fingerprint
        with pytest.raises(PdfExportError):
            validate_redaction_ocr_report(changed, result)
    with pytest.raises(PdfExportError):
        validate_redaction_ocr_report(value, replace(result, request_sha256="d" * 64))
    with pytest.raises(PdfExportError):
        validate_redaction_ocr_report(
            value,
            replace(result, reconstruction=replace(result.reconstruction, source_sha256="e" * 64)),
        )


@pytest.mark.parametrize("status", ["verified", "applied", "failed", "unapplied", ""])
def test_ocr_evidence_cannot_promote_pipeline_status(status: str) -> None:
    with pytest.raises(PdfExportError):
        replace(_report(RedactionOcrRequest(request())), status=status)


@pytest.mark.parametrize("member", ["ocrmypdf_version", "tesseract_version"])
def test_only_admitted_engine_versions_are_reportable(member: str) -> None:
    value = _report(RedactionOcrRequest(request())).to_record()
    for version in ("0", "future", None, True):
        with pytest.raises(PdfExportError):
            RedactionOcrReport.from_record({**value, member: version})


@pytest.mark.parametrize("byte_size", [0, -1, True, 1.0, 1 << 53])
def test_report_sizes_are_bounded_exact_integers(byte_size: object) -> None:
    value = _report(RedactionOcrRequest(request())).to_record()
    with pytest.raises(PdfExportError):
        RedactionOcrReport.from_record({**value, "output_byte_size": byte_size})


def test_representations_and_failures_hide_source_values() -> None:
    value = RedactionOcrRequest(request())
    result = _report(value)
    for rendered in (str(value), repr(value), str(result), repr(result)):
        for forbidden in (
            value.fingerprint,
            str(value.reconstruction.case_id),
            str(value.reconstruction.page_id),
            value.reconstruction.source_sha256,
            result.output_sha256,
            "width_points",
        ):
            assert forbidden not in rendered
    with pytest.raises(PdfExportError) as caught:
        RedactionOcrRequest.from_record({**value.to_record(), "language_set_id": "sensitive-token"})
    assert "sensitive-token" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    with pytest.raises(PdfExportError):
        replace(result, unsigned_derivative=False)

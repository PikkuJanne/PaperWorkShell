"""Versioned reconstruction evidence cannot promote intent or authorize export."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import pytest

from paperworkshell.domain.page_coordinates import ClockwiseRotation, NormalizedPageBox
from paperworkshell.domain.pdf_export import PdfExportError
from paperworkshell.domain.redaction import (
    PERMANENT_REDACTION_ENABLED,
    RedactionError,
    RedactionMark,
    RedactionMarkState,
    transition_redaction_state,
)
from paperworkshell.domain.redaction_reconstruction import (
    RedactionReconstructionReport,
    RedactionReconstructionRequest,
    validate_reconstruction_report,
)


def _id(number: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{number:012d}")


def request() -> RedactionReconstructionRequest:
    return RedactionReconstructionRequest(
        case_id=_id(1),
        manifest_revision=3,
        source_id=_id(2),
        page_id=_id(3),
        source_sha256="a" * 64,
        source_byte_size=100,
        source_page_index=0,
        source_page_count=1,
        marks=(RedactionMark(_id(4), _id(3), NormalizedPageBox(100, 200, 300, 400)),),
    )


def report(value: RedactionReconstructionRequest) -> RedactionReconstructionReport:
    return RedactionReconstructionReport(
        request_sha256=value.fingerprint,
        page_id=value.page_id,
        source_sha256=value.source_sha256,
        output_sha256="b" * 64,
        output_byte_size=400,
        width_pixels=500,
        height_pixels=600,
        width_points=120.0,
        height_points=144.0,
        mark_count=len(value.marks),
    )


def test_request_and_report_roundtrip_are_closed_and_versioned() -> None:
    value = request()
    assert RedactionReconstructionRequest.from_record(value.to_record()) == value
    result = report(value)
    assert RedactionReconstructionReport.from_record(result.to_record()) == result
    validate_reconstruction_report(value, result)
    for version in (0, 2, True):
        with pytest.raises(PdfExportError):
            RedactionReconstructionRequest.from_record(
                {**value.to_record(), "request_version": version}
            )
        with pytest.raises(PdfExportError):
            RedactionReconstructionReport.from_record(
                {**result.to_record(), "report_version": version}
            )
    for extra in ("path", "script", "verified"):
        with pytest.raises(PdfExportError):
            RedactionReconstructionRequest.from_record({**value.to_record(), extra: "unsafe"})
        with pytest.raises(PdfExportError):
            RedactionReconstructionReport.from_record({**result.to_record(), extra: "unsafe"})


def test_fingerprint_binds_every_processing_and_intent_fact() -> None:
    value = request()
    variants = (
        replace(value, case_id=_id(11)),
        replace(value, source_id=_id(12)),
        replace(value, manifest_revision=4),
        replace(value, source_sha256="c" * 64),
        replace(value, source_byte_size=101),
        replace(value, source_page_count=2),
        replace(value, crop=NormalizedPageBox(100, 100, 900000, 900000)),
        replace(value, rotation=ClockwiseRotation.DEGREES_90),
        replace(value, marks=(replace(value.marks[0], box=NormalizedPageBox.full_page()),)),
        replace(value, marks=(replace(value.marks[0], mark_id=_id(13)),)),
    )
    for variant in variants:
        assert variant.fingerprint != value.fingerprint
        with pytest.raises(PdfExportError):
            validate_reconstruction_report(variant, report(value))


@pytest.mark.parametrize("status", ["applied", "verified", "failed", "unapplied", ""])
def test_report_cannot_claim_release_verification(status: str) -> None:
    with pytest.raises(PdfExportError):
        replace(report(request()), status=status)
    assert PERMANENT_REDACTION_ENABLED is True
    with pytest.raises(RedactionError):
        transition_redaction_state(RedactionMarkState.UNAPPLIED, RedactionMarkState.APPLIED)


def test_request_requires_removal_marks_from_exact_page() -> None:
    value = request()
    for records in ((), (replace(value.marks[0], page_id=_id(8)),), value.marks * 2):
        with pytest.raises(PdfExportError):
            replace(value, marks=records)
    with pytest.raises(PdfExportError):
        replace(value, marks=(replace(value.marks[0], mark_id=value.case_id),))
    with pytest.raises(PdfExportError):
        replace(value, source_page_index=1)


def test_representations_hide_source_identifiers_geometry_and_digests() -> None:
    value, result = request(), report(request())
    for rendered in (str(value), repr(value), str(result), repr(result)):
        for token in (
            str(value.case_id),
            str(value.page_id),
            value.source_sha256,
            result.output_sha256,
            "120.0",
        ):
            assert token not in rendered
    with pytest.raises(PdfExportError) as caught:
        RedactionReconstructionRequest.from_record(
            {**value.to_record(), "source_sha256": "synthetic private token"}
        )
    assert "synthetic private token" not in str(caught.value)
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("field_name", ["width_points", "height_points"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), 0.0, -1.0, True])
def test_report_rejects_ambiguous_geometry(field_name: str, value: float | bool) -> None:
    record = report(request()).to_record()
    record[field_name] = value
    with pytest.raises(PdfExportError):
        RedactionReconstructionReport.from_record(record)


def test_unsigned_derivative_is_required_even_when_source_has_no_signature() -> None:
    result = report(request())
    assert result.unsigned_derivative is True
    assert result.source_signature_detected is False
    with pytest.raises(PdfExportError):
        replace(result, unsigned_derivative=False)

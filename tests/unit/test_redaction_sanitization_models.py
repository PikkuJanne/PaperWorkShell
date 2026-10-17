"""Sanitization evidence is closed, value-free and cannot approve publication."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import pytest

from paperworkshell.domain.pdf_export import PdfExportError
from paperworkshell.domain.redaction import PERMANENT_REDACTION_ENABLED
from paperworkshell.domain.redaction_sanitization import (
    RedactionSanitizationInventory,
    RedactionSanitizationReport,
    RedactionSanitizationRequest,
    validate_sanitization_report,
)


def _id(number: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{number:012d}")


def request() -> RedactionSanitizationRequest:
    return RedactionSanitizationRequest(_id(1), _id(2), "a" * 64, 100, 1)


def report(value: RedactionSanitizationRequest) -> RedactionSanitizationReport:
    return RedactionSanitizationReport(
        request_sha256=value.fingerprint,
        source_sha256=value.source_sha256,
        output_sha256="b" * 64,
        output_byte_size=400,
        page_count=value.source_page_count,
        before=RedactionSanitizationInventory(attachments=1, annotations=2, metadata=3),
        after=RedactionSanitizationInventory(),
    )


def test_request_and_report_roundtrip_are_closed_and_versioned() -> None:
    value = request()
    assert RedactionSanitizationRequest.from_record(value.to_record()) == value
    result = report(value)
    assert RedactionSanitizationReport.from_record(result.to_record()) == result
    validate_sanitization_report(value, result)
    for version in (0, 2, True):
        with pytest.raises(PdfExportError):
            RedactionSanitizationRequest.from_record(
                {**value.to_record(), "request_version": version}
            )
        with pytest.raises(PdfExportError):
            RedactionSanitizationReport.from_record(
                {**result.to_record(), "report_version": version}
            )
    for extra in ("path", "script", "verified", "safe_fields"):
        with pytest.raises(PdfExportError):
            RedactionSanitizationRequest.from_record({**value.to_record(), extra: "unsafe"})
        with pytest.raises(PdfExportError):
            RedactionSanitizationReport.from_record({**result.to_record(), extra: "unsafe"})
        with pytest.raises(PdfExportError):
            RedactionSanitizationInventory.from_record({**result.before.to_record(), extra: 1})


def test_fingerprint_binds_every_input_processing_fact() -> None:
    value = request()
    for variant in (
        replace(value, case_id=_id(11)),
        replace(value, source_id=_id(12)),
        replace(value, source_sha256="c" * 64),
        replace(value, source_byte_size=101),
        replace(value, source_page_count=2),
    ):
        assert variant.fingerprint != value.fingerprint
        with pytest.raises(PdfExportError):
            validate_sanitization_report(variant, report(value))
    with pytest.raises(PdfExportError):
        replace(value, profile_id="permissive")


@pytest.mark.parametrize("status", ["applied", "verified", "failed", "unapplied", ""])
def test_report_cannot_claim_release_verification(status: str) -> None:
    with pytest.raises(PdfExportError):
        replace(report(request()), status=status)
    assert PERMANENT_REDACTION_ENABLED is False


@pytest.mark.parametrize("count", [True, -1, 1.0, "private value", 2**63])
def test_inventory_rejects_values_and_ambiguous_counts(count: object) -> None:
    with pytest.raises(PdfExportError):
        RedactionSanitizationInventory.from_record(
            {**RedactionSanitizationInventory().to_record(), "metadata": count}
        )


@pytest.mark.parametrize("name", RedactionSanitizationInventory.__dataclass_fields__)
def test_report_requires_empty_after_inventory(name: str) -> None:
    with pytest.raises(PdfExportError):
        replace(report(request()), after=RedactionSanitizationInventory(**{name: 1}))


def test_lease_facts_and_unsigned_evidence_are_strict() -> None:
    value = request()
    for name, invalid in (
        ("case_id", value.case_id),
        ("source_id", str(value.case_id)),
        ("source_byte_size", True),
        ("source_byte_size", 0),
        ("source_page_count", 0),
        ("source_page_count", 4097),
        ("source_sha256", "private source token"),
    ):
        with pytest.raises(PdfExportError):
            RedactionSanitizationRequest.from_record({**value.to_record(), name: invalid})
    for name, invalid in (
        ("unsigned_derivative", False),
        ("page_count", 0),
        ("output_byte_size", 0),
    ):
        with pytest.raises(PdfExportError):
            RedactionSanitizationReport.from_record({**report(value).to_record(), name: invalid})


def test_representations_and_errors_hide_identifiers_and_digests() -> None:
    value, result = request(), report(request())
    for rendered in (str(value), repr(value), str(result), repr(result)):
        for token in (
            str(value.case_id),
            str(value.source_id),
            value.source_sha256,
            result.output_sha256,
        ):
            assert token not in rendered
    with pytest.raises(PdfExportError) as caught:
        RedactionSanitizationRequest.from_record(
            {**value.to_record(), "source_sha256": "private source token"}
        )
    assert "private source token" not in str(caught.value)
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None

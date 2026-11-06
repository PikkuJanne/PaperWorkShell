"""Strict result facts and value-free removal-export reports."""

from __future__ import annotations

import json
from dataclasses import replace
from functools import partial
from uuid import UUID

import pytest

from paperworkshell.domain.pdf_export import PdfExportError
from paperworkshell.domain.redaction import PERMANENT_REDACTION_ENABLED
from paperworkshell.domain.redaction_export import (
    RedactionExportCode,
    RedactionExportReport,
    RedactionExportStatus,
)
from paperworkshell.domain.redaction_verification import (
    RedactionVerificationReport,
    RedactionVerificationRequest,
    VerificationCode,
    VerificationMethod,
    VerificationMethodResult,
    VerificationStatus,
    run_verification_methods,
)

CASE = UUID("a0000000-0000-4000-8000-000000000001")
PAGES = (
    UUID("a0000000-0000-4000-8000-000000000002"),
    UUID("a0000000-0000-4000-8000-000000000003"),
)


def verification(
    method: VerificationMethod | None = None,
    status: VerificationStatus = VerificationStatus.PASS,
) -> RedactionVerificationReport:
    request = RedactionVerificationRequest("a" * 64, 10, "b" * 64, 10, 2, (0, 1), "c" * 64)
    runners = {
        item: partial(
            VerificationMethodResult, item, VerificationStatus.PASS, VerificationCode.CLEAR
        )
        for item in VerificationMethod
    }
    if method is not None:
        if status is VerificationStatus.UNKNOWN:
            del runners[method]
        elif status is VerificationStatus.FAIL:
            runners[method] = partial(
                VerificationMethodResult, method, status, VerificationCode.CANARY
            )
    return run_verification_methods(request, runners)


def report(
    code: RedactionExportCode = RedactionExportCode.VERIFICATION_UNKNOWN,
    evidence: RedactionVerificationReport | None = None,
    status: RedactionExportStatus = RedactionExportStatus.BLOCKED,
) -> RedactionExportReport:
    return RedactionExportReport(CASE, 4, PAGES, evidence, status, code)


def test_absent_evidence_names_every_method_unknown_without_claiming_removal() -> None:
    result = report()
    assert tuple(item.method for item in result.methods) == tuple(VerificationMethod)
    assert all(item.status is VerificationStatus.UNKNOWN for item in result.methods)
    assert all(item.code is VerificationCode.MISSING for item in result.methods)
    assert "Saved marks remain unapplied" in result.user_text
    assert "unsigned derivative" in result.user_text
    assert "redacted" not in result.user_text.lower()
    assert result.retry_allowed and result.review_allowed


@pytest.mark.parametrize("method", list(VerificationMethod))
def test_every_failed_method_is_reported_and_cannot_be_complete(method: VerificationMethod) -> None:
    evidence = verification(method, VerificationStatus.FAIL)
    result = report(RedactionExportCode.VERIFICATION_FAILED, evidence)
    assert method.value in result.to_audit_text()
    assert VerificationCode.CANARY.value in result.to_audit_text()
    assert result.status is RedactionExportStatus.BLOCKED
    assert "No verified removal export was committed" in result.safe_message
    with pytest.raises(PdfExportError):
        report(RedactionExportCode.COMPLETE, evidence, RedactionExportStatus.COMMITTED)


@pytest.mark.parametrize("method", list(VerificationMethod))
def test_every_unknown_method_blocks_complete_report(method: VerificationMethod) -> None:
    evidence = verification(method, VerificationStatus.UNKNOWN)
    result = report(RedactionExportCode.VERIFICATION_UNKNOWN, evidence)
    assert result.status is RedactionExportStatus.BLOCKED
    with pytest.raises(PdfExportError):
        report(RedactionExportCode.COMPLETE, evidence, RedactionExportStatus.COMMITTED)


def test_complete_methods_do_not_infer_publication_or_approve_release() -> None:
    result = report(RedactionExportCode.RELEASE_PENDING, verification())
    assert result.status is RedactionExportStatus.BLOCKED
    assert not result.retry_allowed and result.review_allowed
    assert "independent security approval" in result.safe_message
    assert PERMANENT_REDACTION_ENABLED is True
    assert not hasattr(result, "export_allowed")


def test_disabled_pipeline_without_run_methods_reports_unknown_and_no_retry() -> None:
    result = report(RedactionExportCode.RELEASE_PENDING)
    assert all(item.status is VerificationStatus.UNKNOWN for item in result.methods)
    assert not result.retry_allowed and result.review_allowed
    assert "only checks actually performed" in result.safe_message


@pytest.mark.parametrize(
    "status,code",
    [
        (RedactionExportStatus.NOT_COMMITTED, RedactionExportCode.CANCELLED),
        (RedactionExportStatus.NOT_COMMITTED, RedactionExportCode.TIMED_OUT),
        (RedactionExportStatus.NOT_COMMITTED, RedactionExportCode.DESTINATION_FAILED),
        (RedactionExportStatus.NOT_COMMITTED, RedactionExportCode.DESTINATION_EXISTS),
        (RedactionExportStatus.COMMITTED, RedactionExportCode.COMPLETE),
        (RedactionExportStatus.UNCERTAIN, RedactionExportCode.DESTINATION_UNCERTAIN),
        (RedactionExportStatus.BLOCKED, RedactionExportCode.STALE_INTENT),
        (RedactionExportStatus.BLOCKED, RedactionExportCode.RESULT_INVALID),
    ],
)
def test_supported_states_round_trip_with_exact_private_evidence(
    status: RedactionExportStatus, code: RedactionExportCode
) -> None:
    result = report(code, verification(), status)
    assert RedactionExportReport.from_record(result.to_record()) == result
    assert len(result.methods) == 7
    if status is RedactionExportStatus.UNCERTAIN:
        assert not result.retry_allowed and result.review_allowed
    elif status is RedactionExportStatus.COMMITTED:
        assert not result.retry_allowed and not result.review_allowed


def test_audit_and_user_output_excludes_internal_digests_and_arbitrary_values() -> None:
    result = report(RedactionExportCode.RELEASE_PENDING, verification())
    text = result.user_text + result.to_audit_text() + repr(result)
    assert all(str(page_id) in result.user_text for page_id in PAGES)
    for token in ("a" * 64, "b" * 64, "c" * 64, "candidate_sha256", "request_sha256"):
        assert token not in text
    audit = result.to_audit_record()
    assert set(audit) == {
        "report_version",
        "case_id",
        "manifest_revision",
        "affected_page_ids",
        "affected_page_count",
        "status",
        "code",
        "unsigned_derivative",
        "methods",
    }
    assert audit["affected_page_count"] == 2
    record = result.to_record()
    record["source_text"] = "SYNTHETIC_PRIVATE_REPORT_TOKEN"
    with pytest.raises(PdfExportError) as caught:
        RedactionExportReport.from_record(record)
    assert "SYNTHETIC_PRIVATE_REPORT_TOKEN" not in str(caught.value) + repr(caught.value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("case_id", "private path"),
        ("case_id", "a0000000-0000-1000-8000-000000000001"),
        ("manifest_revision", True),
        ("manifest_revision", -1),
        ("manifest_revision", 2**53),
        ("affected_page_ids", []),
        ("affected_page_ids", [str(PAGES[0]), str(PAGES[0])]),
        ("affected_page_ids", [str(CASE)]),
        ("affected_page_ids", [str(PAGES[0])] * 4097),
        ("affected_page_ids", ["PRIVATE_PATH"]),
        ("affected_page_ids", [str(PAGES[0]).upper()]),
        ("affected_page_ids", "PRIVATE_PATH"),
        ("verification", {}),
        ("status", "verified"),
        ("code", "PRIVATE_PATH"),
        ("report_version", True),
        ("report_version", 0),
        ("report_version", 3),
        ("unsigned_derivative", False),
        ("unsigned_derivative", 1),
    ],
)
def test_unknown_versions_invalid_records_and_sensitive_values_fail_closed(
    field: str, value: object
) -> None:
    record = report().to_record()
    record[field] = value
    with pytest.raises(PdfExportError):
        RedactionExportReport.from_record(record)


@pytest.mark.parametrize("field", list(RedactionExportReport.__dataclass_fields__))
def test_missing_fields_fail_closed(field: str) -> None:
    record = report().to_record()
    del record[field]
    with pytest.raises(PdfExportError):
        RedactionExportReport.from_record(record)


@pytest.mark.parametrize("status", list(RedactionExportStatus))
@pytest.mark.parametrize("code", list(RedactionExportCode))
def test_status_code_combinations_are_closed(
    status: RedactionExportStatus, code: RedactionExportCode
) -> None:
    valid = {
        (RedactionExportStatus.BLOCKED, RedactionExportCode.RELEASE_PENDING),
        (RedactionExportStatus.BLOCKED, RedactionExportCode.STALE_INTENT),
        (RedactionExportStatus.BLOCKED, RedactionExportCode.RESULT_INVALID),
        (RedactionExportStatus.NOT_COMMITTED, RedactionExportCode.CANCELLED),
        (RedactionExportStatus.NOT_COMMITTED, RedactionExportCode.TIMED_OUT),
        (RedactionExportStatus.NOT_COMMITTED, RedactionExportCode.DESTINATION_FAILED),
        (RedactionExportStatus.NOT_COMMITTED, RedactionExportCode.DESTINATION_EXISTS),
        (RedactionExportStatus.COMMITTED, RedactionExportCode.COMPLETE),
        (RedactionExportStatus.UNCERTAIN, RedactionExportCode.DESTINATION_UNCERTAIN),
    }
    if (status, code) in valid:
        report(code, verification(), status)
    else:
        with pytest.raises(PdfExportError):
            report(code, verification(), status)


def test_mixed_failure_and_unknown_reports_cannot_hide_failure() -> None:
    evidence = verification(VerificationMethod.TEXT, VerificationStatus.FAIL)
    methods = tuple(
        VerificationMethodResult(item.method, VerificationStatus.UNKNOWN, VerificationCode.MISSING)
        if item.method is VerificationMethod.OCR
        else item
        for item in evidence.methods
    )
    changed = replace(evidence, methods=methods)
    with pytest.raises(PdfExportError):
        report(RedactionExportCode.VERIFICATION_UNKNOWN, changed)
    assert report(RedactionExportCode.VERIFICATION_FAILED, changed).retry_allowed


def test_mutated_nested_evidence_is_revalidated_before_any_output() -> None:
    evidence = verification()
    result = report(RedactionExportCode.RELEASE_PENDING, evidence)
    object.__setattr__(evidence.methods[0], "code", "PRIVATE_DOCUMENT_TOKEN")
    for operation in (result.to_record, result.to_audit_record, lambda: result.user_text):
        with pytest.raises(PdfExportError) as caught:
            operation()
        assert "PRIVATE_DOCUMENT_TOKEN" not in str(caught.value)


def test_private_round_trip_is_json_serializable() -> None:
    value = report().to_record()
    assert RedactionExportReport.from_record(json.loads(json.dumps(value))) == report()

"""Verification completeness, correlation, and content-free result contracts."""

from __future__ import annotations

import json
from dataclasses import replace
from functools import partial
from typing import Any

import pytest

from paperworkshell.domain.pdf_export import PdfExportError
from paperworkshell.domain.redaction_verification import (
    VERIFICATION_PROFILE,
    VIEWER_VERIFICATION_PROFILE,
    RedactionVerificationReport,
    RedactionVerificationRequest,
    VerificationCode,
    VerificationMethod,
    VerificationMethodResult,
    VerificationStatus,
    run_verification_methods,
    validate_verification_report,
)


def _request() -> RedactionVerificationRequest:
    return RedactionVerificationRequest("a" * 64, 1024, "b" * 64, 1024, 2, (0, 1), "c" * 64)


def _pass(method: VerificationMethod) -> VerificationMethodResult:
    return VerificationMethodResult(method, VerificationStatus.PASS, VerificationCode.CLEAR)


def test_missing_methods_never_authorize_automated_or_release_result() -> None:
    report = run_verification_methods(_request(), {})
    assert not report.automated_passed and not report.release_ready
    assert all(item.status is VerificationStatus.UNKNOWN for item in report.methods)


@pytest.mark.parametrize("missing", list(VerificationMethod))
def test_every_required_missing_method_blocks_release(missing: VerificationMethod) -> None:
    runners = {
        method: partial(_pass, method) for method in VerificationMethod if method is not missing
    }
    report = run_verification_methods(_request(), runners)
    assert not report.release_ready
    assert report.automated_passed == (
        missing in {VerificationMethod.OCR, VerificationMethod.VIEWERS}
    )


@pytest.mark.parametrize("method", list(VerificationMethod))
def test_any_recovered_canary_blocks_result(method: VerificationMethod) -> None:
    runners = {item: partial(_pass, item) for item in VerificationMethod}
    runners[method] = partial(
        VerificationMethodResult, method, VerificationStatus.FAIL, VerificationCode.CANARY
    )
    report = run_verification_methods(_request(), runners)
    assert not report.release_ready
    assert VerificationCode.CANARY in {item.code for item in report.methods}


@pytest.mark.parametrize(
    "forgery", ["wrong_method", "dict", "exception", "mutated", "unknown_code"]
)
def test_bad_runner_results_are_sanitized_and_fail_closed(forgery: str) -> None:
    def hostile() -> Any:
        if forgery == "wrong_method":
            return _pass(VerificationMethod.OCR)
        if forgery == "dict":
            return {"method": "pass", "text": "PRIVATE_SYNTHETIC_VALUE"}
        if forgery == "exception":
            raise ValueError("PRIVATE_SYNTHETIC_VALUE")
        result = _pass(VerificationMethod.TEXT)
        object.__setattr__(
            result,
            "code",
            "PRIVATE_SYNTHETIC_VALUE" if forgery == "unknown_code" else VerificationCode.CANARY,
        )
        return result

    report = run_verification_methods(_request(), {VerificationMethod.TEXT: hostile})
    assert not report.release_ready
    assert "PRIVATE_SYNTHETIC_VALUE" not in json.dumps(report.to_record()) + repr(report)
    assert report.methods[1].code is VerificationCode.INVALID


def test_closed_records_round_trip_and_bind_exact_candidate_and_intent() -> None:
    request = _request()
    report = run_verification_methods(
        request, {method: partial(_pass, method) for method in VerificationMethod}
    )
    assert report.release_ready and report.automated_passed
    assert RedactionVerificationRequest.from_record(request.to_record()) == request
    assert RedactionVerificationReport.from_record(report.to_record()) == report
    validate_verification_report(request, report)
    for changed in (
        replace(request, candidate_sha256="d" * 64),
        replace(request, intent_sha256="d" * 64),
        replace(request, affected_pages=(0,)),
    ):
        with pytest.raises(PdfExportError):
            validate_verification_report(changed, report)


def test_viewer_profile_is_distinct_explicit_and_cannot_rebind_canonical_evidence() -> None:
    canonical = _request()
    viewer = replace(canonical, profile_id=VIEWER_VERIFICATION_PROFILE)
    assert canonical.profile_id == VERIFICATION_PROFILE
    assert canonical.to_record()["request_version"] == viewer.to_record()["request_version"] == 1
    assert canonical.fingerprint != viewer.fingerprint
    assert RedactionVerificationRequest.from_record(viewer.to_record()) == viewer
    assert VIEWER_VERIFICATION_PROFILE in repr(viewer)
    for request, other in ((canonical, viewer), (viewer, canonical)):
        report = run_verification_methods(
            request, {method: partial(_pass, method) for method in VerificationMethod}
        )
        validate_verification_report(request, report)
        with pytest.raises(PdfExportError):
            validate_verification_report(other, report)


@pytest.mark.parametrize(
    "field,value",
    [
        ("candidate_sha256", "private"),
        ("page_count", True),
        ("affected_pages", (1, 0)),
        ("affected_pages", (2,)),
        ("affected_pages", (0, 0)),
        ("request_version", 2),
        ("profile_id", "future"),
    ],
)
def test_unknown_and_invalid_request_fails_before_work(field: str, value: object) -> None:
    record = _request().to_record()
    record[field] = list(value) if field == "affected_pages" else value  # type: ignore[call-overload]
    with pytest.raises(PdfExportError):
        RedactionVerificationRequest.from_record(record)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "order", "future", "extra", "signed"])
def test_report_cannot_hide_or_substitute_methods(mutation: str) -> None:
    report = run_verification_methods(_request(), {})
    record: dict[str, Any] = report.to_record()
    if mutation == "missing":
        record["methods"].pop()
    elif mutation == "duplicate":
        record["methods"][-1] = record["methods"][0]
    elif mutation == "order":
        record["methods"].reverse()
    elif mutation == "future":
        record["report_version"] = 2
    elif mutation == "signed":
        record["unsigned_derivative"] = False
    else:
        record["content"] = "PRIVATE_SYNTHETIC_VALUE"
    with pytest.raises(PdfExportError):
        RedactionVerificationReport.from_record(record)

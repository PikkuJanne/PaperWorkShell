"""Exact-file runtime checks remain separate from release viewer qualification."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from paperworkshell.app import redaction_export as service_module
from paperworkshell.domain.pdf_export import PdfExportError
from paperworkshell.domain.redaction_export import (
    RUNTIME_EXPORT_REPORT_VERSION,
    RedactionExportCode,
    RedactionExportReport,
    RedactionExportStatus,
)
from paperworkshell.domain.redaction_export_candidate import RedactionExportWorkerReport
from paperworkshell.domain.redaction_reconstruction import RedactionReconstructionRequest
from paperworkshell.domain.redaction_verification import (
    RUNTIME_METHODS,
    RUNTIME_VERIFICATION_PROFILE,
    RedactionVerificationReport,
    RedactionVerificationRequest,
    VerificationCode,
    VerificationMethod,
    VerificationMethodResult,
    VerificationStatus,
)
from tests.unit.test_redaction_export_report import CASE, PAGES, verification
from tests.unit.test_redaction_export_service import _Harness, _worker_report


def _runtime_evidence() -> RedactionVerificationReport:
    return verification(VerificationMethod.VIEWERS, VerificationStatus.UNKNOWN)


def _runtime_report(evidence: RedactionVerificationReport) -> RedactionExportReport:
    return RedactionExportReport(
        CASE,
        4,
        PAGES,
        evidence,
        RedactionExportStatus.COMMITTED,
        RedactionExportCode.COMPLETE,
        report_version=RUNTIME_EXPORT_REPORT_VERSION,
    )


def test_runtime_profile_is_explicit_and_old_all_seven_semantics_are_preserved() -> None:
    request = RedactionVerificationRequest(
        "a" * 64,
        10,
        "b" * 64,
        10,
        1,
        (0,),
        "c" * 64,
        profile_id=RUNTIME_VERIFICATION_PROFILE,
    )
    assert RedactionVerificationRequest.from_record(request.to_record()) == request
    evidence = _runtime_evidence()
    assert evidence.runtime_ready
    assert not evidence.release_ready
    assert verification().release_ready
    assert not verification().runtime_ready


def test_v2_names_runtime_methods_without_claiming_candidate_viewer_observations() -> None:
    result = _runtime_report(_runtime_evidence())
    assert RedactionExportReport.from_record(result.to_record()) == result
    assert result.verification_profile_id == RUNTIME_VERIFICATION_PROFILE
    audit = result.to_audit_record()
    assert audit["required_methods"] == [method.value for method in RUNTIME_METHODS]
    assert audit["viewer_matrix_scope"] == "release_profile"
    assert "This file was not opened in the named viewer matrix" in result.user_text
    assert "Viewer matrix: pass" not in result.user_text
    assert result.methods[-1].status is VerificationStatus.UNKNOWN
    with pytest.raises(PdfExportError):
        replace(result, report_version=1)
    with pytest.raises(PdfExportError):
        replace(result, report_version=3)


@pytest.mark.parametrize("method", RUNTIME_METHODS)
@pytest.mark.parametrize("status", (VerificationStatus.UNKNOWN, VerificationStatus.FAIL))
def test_every_required_runtime_method_still_blocks_publication(
    method: VerificationMethod, status: VerificationStatus
) -> None:
    evidence = _runtime_evidence()
    changed = replace(
        evidence,
        methods=tuple(
            VerificationMethodResult(
                item.method,
                status,
                VerificationCode.MISSING
                if status is VerificationStatus.UNKNOWN
                else VerificationCode.CANARY,
            )
            if item.method is method
            else item
            for item in evidence.methods
        ),
    )
    assert not changed.runtime_ready
    with pytest.raises(PdfExportError):
        _runtime_report(changed)


def test_v2_rejects_invented_pass_or_failed_viewer_evidence() -> None:
    with pytest.raises(PdfExportError):
        _runtime_report(verification())
    with pytest.raises(PdfExportError):
        _runtime_report(verification(VerificationMethod.VIEWERS, VerificationStatus.FAIL))


def _configure_runtime(harness: _Harness) -> None:
    def factory(request: RedactionReconstructionRequest) -> RedactionExportWorkerReport:
        legacy = _worker_report(
            request, method=VerificationMethod.VIEWERS, status=VerificationStatus.UNKNOWN
        )
        bound = replace(legacy.verification_request, profile_id=RUNTIME_VERIFICATION_PROFILE)
        return replace(
            legacy,
            verification_request=bound,
            verification=replace(legacy.verification, request_sha256=bound.fingerprint),
        )

    harness.broker.report_factory = factory


def test_runtime_checks_do_not_approve_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "_release_approved", lambda: False)
    harness = _Harness()
    _configure_runtime(harness)
    path = tmp_path / "output.pdf"
    result = harness.export(path)
    assert result.report_version == RUNTIME_EXPORT_REPORT_VERSION
    assert result.code is RedactionExportCode.RELEASE_PENDING
    assert harness.destination.commit_calls == 0 and not path.exists()
    harness.assert_closed()


def test_qualified_runtime_reaches_real_destination_port_without_fake_viewer_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "_release_approved", lambda: True)
    harness = _Harness()
    _configure_runtime(harness)
    result = harness.export(tmp_path / "output.pdf")
    assert result.code is RedactionExportCode.COMPLETE
    assert result.report_version == RUNTIME_EXPORT_REPORT_VERSION
    assert harness.destination.commit_calls == 1
    assert result.verification is not None and result.verification.runtime_ready
    assert not result.verification.release_ready
    harness.assert_closed()


def test_old_canonical_unknown_viewer_result_cannot_borrow_new_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service_module, "_release_approved", lambda: True)
    harness = _Harness()
    harness.broker.report_factory = lambda request: _worker_report(
        request, method=VerificationMethod.VIEWERS, status=VerificationStatus.UNKNOWN
    )
    result = harness.export(tmp_path / "output.pdf")
    assert result.report_version == 1
    assert result.code is RedactionExportCode.VERIFICATION_UNKNOWN
    assert harness.destination.commit_calls == 0
    harness.assert_closed()

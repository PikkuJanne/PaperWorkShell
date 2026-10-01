"""PWS-0504 bridge rejects untrusted results without leaking private values."""

from __future__ import annotations

from dataclasses import replace

import pytest

from paperworkshell.app.package_pdf_export import (
    PackagePdfCandidateError,
    PackagePdfCandidateErrorCode,
)
from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode
from paperworkshell.domain.pdf_image_stages import PdfImagePageImpact
from tests.unit.test_pdf_image_stages import _analysis
from tests.unit.test_target_size_pdf_export import PROFILE, _TargetStack


def test_target_size_pdf_export_fault_sanitization_closes_sources_and_releases_slot(
    caplog: pytest.LogCaptureFixture,
) -> None:
    private = "PRIVATE APPLICANT SOURCE C:\\Users\\Applicant\\passport.pdf"
    stack = _TargetStack()
    stack.processor.error = RuntimeError(private)
    with pytest.raises(PackagePdfCandidateError) as raised:
        with stack.candidate():
            pass
    assert raised.value.code is PackagePdfCandidateErrorCode.INTERNAL_ERROR
    assert raised.value.__context__ is raised.value.__cause__ is None
    assert private not in str(raised.value) + repr(raised.value) + caplog.text
    assert stack.fallback.sources.batches[0].closed
    stack.processor.error = None
    stack.producer.begin_attempt(None)
    with stack.candidate() as candidate:
        assert private not in repr(candidate.report)


def test_target_size_pdf_export_rejects_worker_report_bound_to_another_plan() -> None:
    stack = _TargetStack()
    with stack.candidate():
        pass
    baseline = stack.processor.candidates[0]
    stack.producer.begin_attempt(None)
    stack.processor.override_report = replace(baseline.report, semantic_fingerprint="c" * 64)
    with pytest.raises(PdfExportError) as raised:
        with stack.candidate():
            pass
    assert raised.value.code is PdfExportErrorCode.REPORT_INVALID
    assert stack.processor.candidates[-1].closed
    assert stack.fallback.sources.batches[-1].closed


def test_target_size_pdf_export_rejects_stale_analysis_before_selected_result_is_exposed() -> None:
    stack = _TargetStack()
    with stack.candidate():
        pass
    baseline = stack.processor.candidates[0]
    stack.producer.begin_attempt(PROFILE)
    # This change does not alter the report's measured byte facts. It still
    # invalidates the complete baseline inventory retained by the application.
    pages = (replace(baseline.analysis.pages[0], image_coverage_ppm=123_456),)
    stack.processor.override_analysis = replace(baseline.analysis, pages=pages)
    with pytest.raises(PdfExportError) as raised:
        with stack.candidate():
            pass
    assert raised.value.code is PdfExportErrorCode.PLAN_STALE
    assert stack.processor.candidates[-1].closed
    assert stack.fallback.sources.batches[-1].closed
    assert stack.producer.stage_reports == ()


def test_target_size_pdf_export_rejects_evidence_page_count_different_from_plan() -> None:
    stack = _TargetStack()
    with stack.candidate():
        pass
    baseline = stack.processor.candidates[0]
    stack.producer.begin_attempt(None)
    stack.processor.override_analysis = replace(
        _analysis(), baseline_sha256=baseline.analysis.baseline_sha256
    )
    stack.processor.override_report = replace(
        baseline.report,
        page_count=2,
        pages=(*baseline.report.pages, PdfImagePageImpact(1, (), 0, 0, 0)),
        verified_page_count=2,
    )
    with pytest.raises(PdfExportError) as raised:
        with stack.candidate():
            pass
    assert raised.value.code is PdfExportErrorCode.REPORT_INVALID
    assert stack.processor.candidates[-1].closed
    assert stack.fallback.sources.batches[-1].closed

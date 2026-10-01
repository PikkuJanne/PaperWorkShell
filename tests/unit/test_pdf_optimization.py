"""Lossless optimization evidence stays bounded and cannot claim a larger selection."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from paperworkshell.domain.manifest_repository import MAX_SAFE_MANIFEST_INTEGER
from paperworkshell.domain.page_operations import MAX_PAGE_COUNT
from paperworkshell.domain.pdf_optimization import (
    PdfOptimizationOutcome,
    PdfOptimizationProfile,
    PdfOptimizationReport,
)


def _optimized() -> PdfOptimizationReport:
    return PdfOptimizationReport(
        profile=PdfOptimizationProfile.SAFE_OBJECT_STREAM_V1,
        outcome=PdfOptimizationOutcome.OPTIMIZED,
        baseline_byte_size=2_048,
        candidate_byte_size=1_024,
        selected_byte_size=1_024,
        verified_page_count=3,
    )


def test_optimized_report_is_immutable_and_claims_only_measured_reduction() -> None:
    report = _optimized()

    assert report.claim == "Safe optimization reduced the verified PDF from 2048 to 1024 bytes."
    assert report.report_version == 1
    with pytest.raises(FrozenInstanceError):
        report.selected_byte_size = 8  # type: ignore[misc]


@pytest.mark.parametrize("candidate_byte_size", (None, 2_048, 2_049))
def test_no_reduction_preserves_baseline_and_claims_no_candidate_verification(
    candidate_byte_size: int | None,
) -> None:
    report = replace(
        _optimized(),
        outcome=PdfOptimizationOutcome.NO_REDUCTION,
        candidate_byte_size=candidate_byte_size,
        selected_byte_size=2_048,
        verified_page_count=0,
    )

    assert report.claim == (
        "Safe optimization kept the verified PDF at 2048 bytes "
        "because no smaller candidate was produced."
    )


def test_budget_skip_has_no_candidate_or_candidate_verification() -> None:
    report = replace(
        _optimized(),
        outcome=PdfOptimizationOutcome.SKIPPED_BUDGET,
        candidate_byte_size=None,
        selected_byte_size=2_048,
        verified_page_count=0,
    )

    assert report.claim == (
        "Safe optimization was skipped because the temporary-storage budget could not "
        "hold a candidate; the verified PDF remains 2048 bytes."
    )


@pytest.mark.parametrize(
    "changes",
    (
        {"profile": "safe_object_stream_v1"},
        {"profile": "PRIVATE_PROFILE_CANARY"},
        {"outcome": "optimized"},
        {"report_version": 2},
        {"report_version": True},
        {"baseline_byte_size": 0},
        {"baseline_byte_size": MAX_SAFE_MANIFEST_INTEGER + 1},
        {"baseline_byte_size": True},
        {"candidate_byte_size": None},
        {"candidate_byte_size": 0},
        {"candidate_byte_size": True},
        {"candidate_byte_size": MAX_SAFE_MANIFEST_INTEGER + 1},
        {"candidate_byte_size": 2_048, "selected_byte_size": 2_048},
        {"candidate_byte_size": 2_049, "selected_byte_size": 2_049},
        {"selected_byte_size": 1_025},
        {"selected_byte_size": True},
        {"selected_byte_size": MAX_SAFE_MANIFEST_INTEGER + 1},
        {"verified_page_count": 0},
        {"verified_page_count": -1},
        {"verified_page_count": True},
        {"verified_page_count": MAX_PAGE_COUNT + 1},
        {
            "outcome": PdfOptimizationOutcome.NO_REDUCTION,
            "selected_byte_size": 2_048,
            "verified_page_count": 0,
        },
        {
            "outcome": PdfOptimizationOutcome.NO_REDUCTION,
            "candidate_byte_size": 2_048,
            "selected_byte_size": 2_048,
        },
        {
            "outcome": PdfOptimizationOutcome.SKIPPED_BUDGET,
            "candidate_byte_size": 2_048,
            "selected_byte_size": 2_048,
            "verified_page_count": 0,
        },
        {
            "outcome": PdfOptimizationOutcome.SKIPPED_BUDGET,
            "candidate_byte_size": None,
            "verified_page_count": 0,
        },
    ),
)
def test_report_rejects_invalid_or_contradictory_evidence(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError) as captured:
        replace(_optimized(), **changes)  # type: ignore[arg-type]

    assert str(captured.value) == "invalid PDF optimization report"
    assert "PRIVATE_PROFILE_CANARY" not in repr(captured.value)

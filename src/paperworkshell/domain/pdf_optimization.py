"""Closed, path-free evidence for conservative PDF object/stream optimization.

The report describes a worker's bounded optimization attempt after baseline
verification. It never authorizes destructive image changes or metadata removal.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from paperworkshell.domain.manifest_repository import MAX_SAFE_MANIFEST_INTEGER
from paperworkshell.domain.page_operations import MAX_PAGE_COUNT

PDF_OPTIMIZATION_REPORT_VERSION: Final = 1


class PdfOptimizationProfile(StrEnum):
    """Fixed lossless object/stream handling with no image re-encoding."""

    SAFE_OBJECT_STREAM_V1 = "safe_object_stream_v1"


class PdfOptimizationOutcome(StrEnum):
    """Why a verified baseline or a separately verified candidate was selected."""

    OPTIMIZED = "optimized"
    NO_REDUCTION = "no_reduction"
    SKIPPED_BUDGET = "skipped_budget"


@dataclass(frozen=True, slots=True)
class PdfOptimizationReport:
    """Numeric evidence only; candidate verification is claimed only on selection.

    ``NO_REDUCTION`` may omit candidate size when no candidate was produced.
    An invalid smaller candidate fails export and never produces this success
    report. A skipped attempt retains the already verified baseline.
    """

    profile: PdfOptimizationProfile
    outcome: PdfOptimizationOutcome
    baseline_byte_size: int
    candidate_byte_size: int | None
    selected_byte_size: int
    verified_page_count: int
    report_version: int = PDF_OPTIMIZATION_REPORT_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.profile) is not PdfOptimizationProfile
            or type(self.outcome) is not PdfOptimizationOutcome
            or type(self.report_version) is not int
            or self.report_version != PDF_OPTIMIZATION_REPORT_VERSION
            or type(self.baseline_byte_size) is not int
            or not 1 <= self.baseline_byte_size <= MAX_SAFE_MANIFEST_INTEGER
            or type(self.selected_byte_size) is not int
            or not 1 <= self.selected_byte_size <= MAX_SAFE_MANIFEST_INTEGER
            or (
                self.candidate_byte_size is not None
                and (
                    type(self.candidate_byte_size) is not int
                    or not 1 <= self.candidate_byte_size <= MAX_SAFE_MANIFEST_INTEGER
                )
            )
            or type(self.verified_page_count) is not int
            or not 0 <= self.verified_page_count <= MAX_PAGE_COUNT
        ):
            raise ValueError("invalid PDF optimization report")
        if self.outcome is PdfOptimizationOutcome.OPTIMIZED:
            valid = (
                self.candidate_byte_size is not None
                and self.candidate_byte_size < self.baseline_byte_size
                and self.selected_byte_size == self.candidate_byte_size
                and self.verified_page_count >= 1
            )
        else:
            valid = (
                self.selected_byte_size == self.baseline_byte_size
                and self.verified_page_count == 0
                and (
                    self.candidate_byte_size is None
                    or (
                        self.outcome is PdfOptimizationOutcome.NO_REDUCTION
                        and self.candidate_byte_size >= self.baseline_byte_size
                    )
                )
            )
        if not valid:
            raise ValueError("invalid PDF optimization report")

    @property
    def claim(self) -> str:
        """Describe the measured outcome without claiming broader fidelity checks."""

        if self.outcome is PdfOptimizationOutcome.OPTIMIZED:
            return (
                "Safe optimization reduced the verified PDF from "
                f"{self.baseline_byte_size} to {self.selected_byte_size} bytes."
            )
        if self.outcome is PdfOptimizationOutcome.NO_REDUCTION:
            return (
                f"Safe optimization kept the verified PDF at {self.selected_byte_size} bytes "
                "because no smaller candidate was produced."
            )
        return (
            "Safe optimization was skipped because the temporary-storage budget could not "
            f"hold a candidate; the verified PDF remains {self.selected_byte_size} bytes."
        )

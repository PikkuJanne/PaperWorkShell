"""TRACE evidence from completed, verified export operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Final
from uuid import UUID

from paperworkshell.app.organizer import OrganizerPagesPdfExportOutcome
from paperworkshell.domain.pdf_export import PdfExportCheckCode
from paperworkshell.domain.pdf_export_history import (
    PdfExportHistoryError,
    decode_pdf_export_history_record,
)
from paperworkshell.domain.trace_recipe import TraceOutputStatus as TraceOutputStatus
from paperworkshell.localization.catalog import mark, tr


class TraceCheckStatus(StrEnum):
    NOT_CHECKED = "not_checked"
    OUT_OF_DATE = "out_of_date"
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"


TRACE_CHECK_LABELS: Final = {
    TraceCheckStatus.NOT_CHECKED: mark("Not checked"),
    TraceCheckStatus.OUT_OF_DATE: mark("Out of date"),
    TraceCheckStatus.PASSED: mark("Passed"),
    TraceCheckStatus.WARNING: mark("Warning"),
    TraceCheckStatus.FAILED: mark("Failed"),
}


@dataclass(frozen=True, slots=True)
class TraceCheck:
    """One scoped statement based on what the existing export measured."""

    name: str
    status: TraceCheckStatus
    finding: str


@dataclass(frozen=True, slots=True)
class TraceExportEvidence:
    """A verified derivative and the exact active plan that produced it."""

    case_id: UUID
    export_id: UUID
    plan_revision: int
    plan_fingerprint: str = field(repr=False)
    worker_fingerprint: str = field(repr=False)
    destination: Path = field(repr=False)
    sha256: str = field(repr=False)
    byte_size: int
    page_ids: tuple[UUID, ...] = field(repr=False)
    make_searchable: bool
    checks: tuple[TraceCheck, ...]
    ocr_language_set_id: str | None = None
    execution_details: tuple[str, ...] = ()

    @property
    def page_count(self) -> int:
        return len(self.page_ids)

    def applies_to(
        self,
        case_id: UUID | None,
        plan_fingerprint: str | None,
        *,
        ocr_language_set_id: str | None = None,
    ) -> bool:
        return (
            self.case_id == case_id
            and self.plan_fingerprint == plan_fingerprint
            and (
                self.ocr_language_set_id is None
                or ocr_language_set_id is None
                or self.ocr_language_set_id == ocr_language_set_id
            )
        )


def trace_export_evidence(
    outcome: OrganizerPagesPdfExportOutcome, destination: Path, *, make_searchable: bool
) -> TraceExportEvidence | None:
    """Adapt the worker verification and destination readback already performed."""

    plan = outcome.state.trace_plan
    committed = outcome.committed_export
    report = committed.report
    page_ids = tuple(page.page_id for page in outcome.state.pages)
    if plan is None or len(page_ids) != report.page_count or not committed.destination_reopened:
        return None
    try:
        history = decode_pdf_export_history_record(committed.history_retry.record_payload)
    except PdfExportHistoryError:
        # Preserve the already-completed export if a receipt cannot be displayed.
        return None
    checks = [
        TraceCheck(
            check.code.value,
            TraceCheckStatus.WARNING
            if check.code is PdfExportCheckCode.UNSIGNED_DERIVATIVE
            else TraceCheckStatus.PASSED,
            check.claim,
        )
        for check in report.checks
    ]
    checks.append(
        TraceCheck(
            PdfExportCheckCode.OUTPUT_COMMIT_REOPENED.value,
            TraceCheckStatus.PASSED,
            tr("The published file was reopened and its measured bytes matched."),
        )
    )
    searchable = outcome.searchable_report
    execution_details: tuple[str, ...] = (
        tr("Output option: make scanned text searchable.")
        if make_searchable
        else tr("Output option: export PDF without adding searchable text."),
    )
    ocr_language_set_id = None
    if searchable is not None:
        profile = searchable.plan.profile
        ocr_language_set_id = profile.ocr_language_set_id
        execution_details += (
            tr("OCR language set: {value1}", value1=profile.ocr_language_set_id),
            tr("OCR processing profile: {value1}", value1=profile.ocr_profile_id),
        )
        checks.append(
            TraceCheck(
                "OCR_TEXT_LAYER",
                TraceCheckStatus.WARNING
                if searchable.needs_review_page_count
                else TraceCheckStatus.PASSED,
                tr(
                    "OCR text verified on {value1} pages. Existing text preserved on {value2} pages. Pages needing review: {value3}.",
                    value1=searchable.ocr_text_page_count,
                    value2=searchable.existing_text_page_count,
                    value3=searchable.needs_review_page_count,
                ),
            )
        )
    checks.extend(
        (
            TraceCheck("OCR_ACCURACY", TraceCheckStatus.NOT_CHECKED, tr("OCR accuracy")),
            TraceCheck("PDF_A", TraceCheckStatus.NOT_CHECKED, tr("PDF/A compliance")),
            TraceCheck(
                "SOURCE_PRESERVATION", TraceCheckStatus.NOT_CHECKED, tr("Source bytes after export")
            ),
        )
    )
    return TraceExportEvidence(
        committed.history_retry.case_id,
        committed.export_id,
        history.manifest_revision,
        plan.fingerprint,
        report.semantic_fingerprint,
        destination,
        report.output_sha256,
        report.output_byte_size,
        page_ids,
        make_searchable,
        tuple(checks),
        ocr_language_set_id,
        execution_details,
    )

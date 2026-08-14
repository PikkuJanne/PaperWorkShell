"""Privacy regressions for the page-quality UI boundary."""

from __future__ import annotations

from uuid import UUID

from PySide6.QtCore import Qt

from paperworkshell.app.quality_analysis import (
    QualityAnalysisError,
    QualityAnalysisErrorCode,
    QualityReviewSnapshot,
)
from paperworkshell.domain.quality_analysis import (
    QualityEvidenceKind,
    QualityFinding,
    QualityFindingCode,
)
from paperworkshell.ui.backend import sanitize_ui_failure
from paperworkshell.ui.models import QualityFindingListModel

CASE_ID = UUID("90300000-0000-4000-8000-000000000001")
PAGE_ID = UUID("90300000-0000-4000-8000-000000000002")
PRIVATE_CANARY = r"APPLICANT_OCR_SECRET C:\records\private-applicant.pdf"


class _HostileQualityError(QualityAnalysisError):
    """Adapter-owned subclass that must not cross as a known exact failure."""


def test_quality_error_sanitizer_rebuilds_exact_errors_and_rejects_subclasses() -> None:
    exact = QualityAnalysisError(
        QualityAnalysisErrorCode.PREVIEW_FAILED,
        "quality.preview",
    )
    exact.safe_message = PRIVATE_CANARY

    sanitized = sanitize_ui_failure(exact, operation_id=31)

    assert sanitized.code == QualityAnalysisErrorCode.PREVIEW_FAILED.value
    assert sanitized.message == (
        "PaperWorkShell could not prepare a page for quality review safely."
    )
    assert sanitized.operation_id == 31
    assert PRIVATE_CANARY not in repr(sanitized)

    subclass = _HostileQualityError(
        QualityAnalysisErrorCode.PREVIEW_FAILED,
        "quality.preview",
    )
    subclass.safe_message = PRIVATE_CANARY
    rejected = sanitize_ui_failure(subclass, operation_id=32)
    assert rejected.code == "PWS_UI_UNEXPECTED"
    assert rejected.operation_id == 32
    assert PRIVATE_CANARY not in repr(rejected)


def test_quality_error_sanitizer_closes_invalid_exact_state() -> None:
    error = QualityAnalysisError(
        QualityAnalysisErrorCode.PREVIEW_FAILED,
        "quality.preview",
    )
    error.code = PRIVATE_CANARY  # type: ignore[assignment]
    error.context = PRIVATE_CANARY
    error.safe_message = PRIVATE_CANARY

    sanitized = sanitize_ui_failure(error)

    assert sanitized.code == QualityAnalysisErrorCode.INTERNAL_ERROR.value
    assert sanitized.message == "PaperWorkShell could not complete page-quality review safely."
    assert PRIVATE_CANARY not in repr(sanitized)


def test_virtual_findings_expose_closed_review_text_not_raw_evidence_or_profile_digest() -> None:
    observed = 1_234_567_890
    threshold = 9_876_543_210
    digest = "d" * 64
    finding = QualityFinding(
        PAGE_ID,
        1,
        QualityFindingCode.LIKELY_BLUR,
        QualityEvidenceKind.LAPLACIAN_MEAN_SQUARE,
        observed,
        threshold,
    )
    snapshot = QualityReviewSnapshot(
        CASE_ID,
        5,
        (PAGE_ID,),
        (finding,),
        "scan-quality-v1",
        digest,
    )
    model = QualityFindingListModel()
    model.set_snapshot(snapshot)
    index = model.index(0, 0)

    exposed_text = " ".join(
        str(model.data(index, role))
        for role in (
            int(Qt.ItemDataRole.DisplayRole),
            int(Qt.ItemDataRole.AccessibleTextRole),
            int(Qt.ItemDataRole.AccessibleDescriptionRole),
            int(Qt.ItemDataRole.ToolTipRole),
        )
    )
    assert "Needs review: Likely blur" in exposed_text
    assert str(observed) not in exposed_text
    assert str(threshold) not in exposed_text
    assert digest not in exposed_text
    assert PRIVATE_CANARY not in exposed_text

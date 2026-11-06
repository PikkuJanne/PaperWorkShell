"""Fixed, no-input public support sample; no filesystem or environment collector."""

from datetime import UTC, datetime
from uuid import UUID

from paperworkshell.diagnostics.bundle import DiagnosticBundlePreview
from paperworkshell.diagnostics.models import (
    DiagnosticCount,
    DiagnosticCountKind,
    DiagnosticFailureCode,
    DiagnosticResource,
    DiagnosticResourceKind,
    DiagnosticVersion,
    DiagnosticVersionKind,
)
from paperworkshell.diagnostics.summary import DiagnosticSummary

SAMPLE_TIME = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def sample_preview() -> DiagnosticBundlePreview:
    return DiagnosticBundlePreview(
        DiagnosticSummary(
            generated_at=SAMPLE_TIME,
            record_count=2,
            versions=(DiagnosticVersion(DiagnosticVersionKind.APPLICATION, (0, 0, 0)),),
            operation_ids=(UUID("10000000-0000-4000-8000-000000000805"),),
            counts=(DiagnosticCount(DiagnosticCountKind.OPERATIONS_COMPLETED, 1),),
            codes=(DiagnosticFailureCode.OPERATION_FAILED,),
            resources=(DiagnosticResource(DiagnosticResourceKind.DURATION_MILLISECONDS, 25),),
        )
    )

"""GUI-safe package coordination over verified producer and destination ports.

The desktop composition root supplies an operation-scoped producer factory.
No document parser, application service, worker-host implementation, or history
schema is imported here. The returned presentation contains explicit user-facing
facts only. Summary text is a separately requested companion, not a portal file
or ZIP member.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from typing import Literal

from paperworkshell.domain.checklist import ChecklistDefinition
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.package_summary import (
    MAX_SUMMARY_TEXT_CODEPOINTS,
    PackageSummary,
    PackageSummaryErrorCode,
    PackageSummaryReviewItem,
    PackageSummaryReviewKind,
    reject_package_summary,
)
from paperworkshell.domain.resource_policy import ResourcePolicy
from paperworkshell.export.package_export import (
    PackageExportErrorCode,
    PackageExportOutcome,
    PackageExportProgress,
    PackagePdfProducerPort,
    export_package_plan,
    reject_package_export,
)
from paperworkshell.export.package_summary import (
    build_package_summary,
    build_package_summary_recovery,
)
from paperworkshell.preflight.engine import PreflightEvaluation
from paperworkshell.preflight.package_planner import (
    PackageExportPlan,
    build_package_export_plan,
)
from paperworkshell.preflight.portal_constraints import portal_constraint_profile_from_template
from paperworkshell.storage.export_destination import WindowsExportDestination
from paperworkshell.templates.models import TemplateSnapshot

type SummarySaveState = Literal["committed", "destination_exists", "not_committed", "unknown"]
type PackageProducerFactory = Callable[[], AbstractContextManager[PackagePdfProducerPort]]


class WindowsPackageExportService:
    """Run one exact reviewed package on its existing coordinator owner thread."""

    __slots__ = ("_destination", "_policy", "_producer_factory")

    def __init__(
        self,
        producer_factory: PackageProducerFactory,
        destination: WindowsExportDestination,
        policy: ResourcePolicy,
    ) -> None:
        if (
            not callable(producer_factory)
            or type(destination) is not WindowsExportDestination
            or type(policy) is not ResourcePolicy
            or destination.minimum_free_space_reserve_bytes
            != policy.minimum_export_free_space_reserve_bytes
        ):
            reject_package_export(PackageExportErrorCode.MODEL_INVALID, "package_service.compose")
        self._producer_factory = producer_factory
        self._destination = destination
        self._policy = policy

    def export(
        self,
        snapshot: ManifestSnapshot,
        template: TemplateSnapshot,
        definition: ChecklistDefinition,
        preflight: PreflightEvaluation,
        plan: PackageExportPlan,
        destination_parent: Path,
        *,
        deadline: Callable[[], bool],
        cancellation: Callable[[], bool],
        progress: Callable[[int, int], None],
        human_review_confirmed: bool = False,
    ) -> PackageSummary:
        """Recheck review authority, then return exact verified publication facts."""

        if (
            not isinstance(destination_parent, Path)
            or not destination_parent.is_absolute()
            or type(plan) is not PackageExportPlan
            or not callable(deadline)
            or not callable(cancellation)
            or not callable(progress)
            or type(human_review_confirmed) is not bool
        ):
            reject_package_export(PackageExportErrorCode.MODEL_INVALID, "package_service.request")
        planned_summary = build_package_summary(
            snapshot, template, definition, preflight, plan=plan
        )
        try:
            parent_units = len(str(destination_parent.absolute()).encode("utf-16-le")) // 2
            rebuilt_plan = build_package_export_plan(
                snapshot,
                definition,
                portal_constraint_profile_from_template(template),
                plan.mode,
                destination_parent_utf16_units=parent_units,
            )
        except Exception:
            reject_package_export(PackageExportErrorCode.PLAN_INVALID, "package_service.plan")
        if rebuilt_plan != plan:
            reject_package_export(PackageExportErrorCode.PLAN_STALE, "package_service.plan")
        if preflight.report.has_blocking_errors or (
            preflight.report.requires_user_confirmation and not human_review_confirmed
        ):
            return planned_summary

        def report_progress(value: PackageExportProgress) -> None:
            progress(value.completed_pages, value.total_pages)

        outcome: PackageExportOutcome | None = None
        cleanup_failed = False
        try:
            with self._producer_factory() as producer:
                outcome = export_package_plan(
                    snapshot,
                    plan,
                    destination_parent,
                    producer,
                    self._destination,
                    self._policy,
                    deadline=deadline,
                    cancellation=cancellation,
                    progress=report_progress,
                )
        except Exception:
            if outcome is None:
                raise
            # Publication facts are already identity-resolved. Losing those
            # facts because later runtime cleanup failed could encourage a
            # duplicate retry or conceal an already committed artifact.
            cleanup_failed = True
        if outcome is None:
            reject_package_export(PackageExportErrorCode.INTERNAL_ERROR, "package_service.outcome")
        try:
            summary = build_package_summary(
                snapshot, template, definition, preflight, plan=plan, outcome=outcome
            )
            if cleanup_failed:
                summary = _with_cleanup_warning(summary)
        except Exception:
            # A detailed presentation failure cannot erase verified destination
            # facts. Recovery presentation is bounded solely by the output plan.
            summary = build_package_summary_recovery(outcome)
            if cleanup_failed:
                summary = _with_cleanup_warning(summary)
        return summary

    def save_summary(
        self,
        summary: PackageSummary,
        destination: Path,
        *,
        deadline: Callable[[], bool],
        cancellation: Callable[[], bool],
    ) -> SummarySaveState:
        """Save precisely visible plain text as an explicit no-replace companion."""

        if (
            type(summary) is not PackageSummary
            or not isinstance(destination, Path)
            or destination.suffix.casefold() != ".txt"
            or not callable(deadline)
            or not callable(cancellation)
        ):
            reject_package_summary(PackageSummaryErrorCode.MODEL_INVALID)
        payload = summary.to_plain_text().encode("utf-8")
        if len(payload) > min(
            4 * MAX_SUMMARY_TEXT_CODEPOINTS,
            self._policy.maximum_temporary_storage_bytes,
        ):
            reject_package_summary(PackageSummaryErrorCode.LIMIT_EXCEEDED)
        with BytesIO(payload) as source:
            result = self._destination.commit(
                destination,
                source,
                expected_byte_size=len(payload),
                expected_sha256=hashlib.sha256(payload).hexdigest(),
                cancellation=cancellation,
                deadline=deadline,
            )
        # Explicit mapping preserves an uncertain result without claiming success.
        if result.committed:
            return "committed"
        if result.state.value == "destination_exists":
            return "destination_exists"
        if result.state.value == "not_committed":
            return "not_committed"
        return "unknown"

    def __repr__(self) -> str:
        return "WindowsPackageExportService(<local verified runtime>)"


def _with_cleanup_warning(summary: PackageSummary) -> PackageSummary:
    result = replace(
        summary,
        review_items=(
            *summary.review_items,
            PackageSummaryReviewItem(
                PackageSummaryReviewKind.WARNING,
                "Package processing",
                "File save results are listed above. Worker cleanup could not be confirmed.",
                "Close PaperWorkShell before further processing and review the saved file inventory.",
            ),
        ),
    )
    result.to_plain_text()
    return result

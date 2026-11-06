"""GUI-safe package coordination over verified producer and destination ports.

The desktop composition root supplies an operation-scoped producer factory.
No document parser, application service, worker-host implementation, or history
schema is imported here. The returned presentation contains explicit user-facing
facts only. Summary text is a separately requested companion, not a portal file
or ZIP member.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

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
from paperworkshell.domain.post_export_deletion import (
    MAX_PRESERVED_EXPORT_ARTIFACTS,
    PostExportDeletionError,
    PostExportDeletionErrorCode,
)
from paperworkshell.domain.resource_policy import ResourcePolicy
from paperworkshell.domain.text_companion import (
    TextCompanionError,
    TextCompanionErrorCode,
    TextCompanionLimits,
    reject_text_companion,
    require_companion_active,
)
from paperworkshell.export.compression_review import (
    WindowsCompressionReviewService as WindowsCompressionReviewService,
)
from paperworkshell.export.package_export import (
    PackageExportErrorCode,
    PackageExportProgress,
    PackagePdfProducerPort,
    export_package_plan,
    reject_package_export,
)
from paperworkshell.export.package_export import (
    PackageExportOutcome as PackageExportOutcome,
)
from paperworkshell.export.package_summary import (
    build_package_summary,
    build_package_summary_recovery,
)
from paperworkshell.localization.catalog import tr
from paperworkshell.preflight.engine import PreflightEvaluation
from paperworkshell.preflight.package_planner import (
    PackageExportPlan,
    build_package_export_plan,
)
from paperworkshell.preflight.portal_constraints import portal_constraint_profile_from_template
from paperworkshell.storage._windows import open_handle
from paperworkshell.storage.export_destination import WindowsExportDestination
from paperworkshell.storage.post_export_deletion import (
    PreservedExportArtifact,
    WindowsRetainedExportGuard,
)
from paperworkshell.templates.models import TemplateSnapshot

type SummarySaveState = Literal["committed", "destination_exists", "not_committed", "unknown"]
type PackageProducerFactory = Callable[[], AbstractContextManager[PackagePdfProducerPort]]
type VerifiedExportObserver = Callable[
    [ManifestSnapshot, tuple[PreservedExportArtifact, ...]], None
]


class TextCompanionExtractor(Protocol):
    def extract_handle(
        self,
        case_id: UUID,
        source_handle: int,
        *,
        expected_byte_size: int,
        expected_sha256: str,
        page_count: int,
        deadline: Callable[[], bool],
        cancellation: Callable[[], bool],
        member_position: int | None = None,
        member_byte_size: int | None = None,
        member_sha256: str | None = None,
    ) -> bytes: ...


class WindowsPackageExportService:
    """Run one exact reviewed package on its existing coordinator owner thread."""

    __slots__ = (
        "_destination",
        "_policy",
        "_producer_factory",
        "_text_extractor",
        "_forbidden_export_root",
        "_verified_export_observer",
        "_verified_export_reset",
        "_verified_snapshot",
        "_verified_outcome",
        "_verified_parent",
        "_preserved_artifacts",
    )

    def __init__(
        self,
        producer_factory: PackageProducerFactory,
        destination: WindowsExportDestination,
        policy: ResourcePolicy,
        *,
        text_extractor: TextCompanionExtractor | None = None,
        forbidden_export_root: Path | None = None,
        verified_export_observer: VerifiedExportObserver | None = None,
        verified_export_reset: Callable[[], None] | None = None,
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
        self._text_extractor = text_extractor
        self._forbidden_export_root = forbidden_export_root
        self._verified_export_observer = verified_export_observer
        self._verified_export_reset = verified_export_reset
        self._verified_snapshot: ManifestSnapshot | None = None
        self._verified_outcome: PackageExportOutcome | None = None
        self._verified_parent: Path | None = None
        self._preserved_artifacts: tuple[PreservedExportArtifact, ...] = ()

    @property
    def last_verified_export_snapshot(self) -> ManifestSnapshot | None:
        return self._verified_snapshot

    @property
    def preserved_export_artifacts(self) -> tuple[PreservedExportArtifact, ...]:
        return self._preserved_artifacts

    def guard_last_verified_export(
        self,
        forbidden_root: Path,
        *,
        cancellation: Callable[[], bool] | None = None,
        deadline: Callable[[], bool] | None = None,
    ) -> AbstractContextManager[None]:
        return WindowsRetainedExportGuard(
            self._preserved_artifacts,
            forbidden_root=forbidden_root,
            cancellation=cancellation,
            deadline=deadline,
        ).guard()

    def clear_verified_export(self) -> None:
        """Discard follow-up authority before a new review or processing attempt."""
        self._verified_snapshot = None
        self._verified_outcome = None
        self._verified_parent = None
        self._preserved_artifacts = ()
        if self._verified_export_reset is not None:
            try:
                self._verified_export_reset()
            except Exception:
                reject_text_companion(TextCompanionErrorCode.EXPORT_REQUIRED)

    def remember_verified_export(
        self, snapshot: ManifestSnapshot, outcome: PackageExportOutcome, destination_parent: Path
    ) -> None:
        """Retain exact publication facts; a summary is never export authority."""
        try:
            self.clear_verified_export()
        except TextCompanionError:
            # Publication already completed. Missing follow-up authority cannot
            # conceal that fact or authorize extraction/deletion of an old case.
            return
        if (
            type(snapshot) is not ManifestSnapshot
            or type(outcome) is not PackageExportOutcome
            or not outcome.successful
            or outcome.destination is None
            or outcome.report.plan.authority.case_id != snapshot.case_id
            or outcome.report.plan.authority.manifest_revision != snapshot.revision
            or outcome.report.plan.authority.manifest_sha256 != snapshot.sha256
            or outcome.report.plan.authority.manifest_cas_token != snapshot.cas_token
        ):
            return
        plan = outcome.report.plan
        names = (
            (plan.archive.filename,)
            if plan.archive is not None
            else tuple(item.filename for item in plan.pdfs)
        )
        artifacts = tuple(
            PreservedExportArtifact(
                destination_parent / name, artifact.byte_size or 0, artifact.sha256 or ""
            )
            for name, artifact in zip(names, outcome.destination.artifacts, strict=True)
        )
        self._verified_snapshot, self._verified_outcome, self._verified_parent = (
            snapshot,
            outcome,
            destination_parent,
        )
        self._preserved_artifacts = artifacts
        self._notify_verified_export()

    def _notify_verified_export(self) -> None:
        if self._verified_snapshot is not None and self._verified_export_observer is not None:
            try:
                self._verified_export_observer(self._verified_snapshot, self._preserved_artifacts)
            except Exception:
                # An optional follow-up cannot conceal a committed package or
                # turn an uncertain notification into new deletion authority.
                self._verified_snapshot = None

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

        self.clear_verified_export()

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
        if outcome.successful and not cleanup_failed:
            self.remember_verified_export(snapshot, outcome, destination_parent)
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
        self._require_optional_destination(destination)
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
            self._remember_optional_output(destination, payload)
            return "committed"
        if result.state.value == "destination_exists":
            return "destination_exists"
        if result.state.value == "not_committed":
            return "not_committed"
        return "unknown"

    def _require_optional_destination(self, destination: Path) -> None:
        if not destination.is_absolute():
            reject_text_companion(TextCompanionErrorCode.INVALID)
        if self._forbidden_export_root is not None:
            boundary = str(self._forbidden_export_root.absolute()).casefold().rstrip("\\/")
            selected = str(destination.absolute()).casefold()
            if (
                selected == boundary
                or selected.startswith(boundary + "\\")
                or selected.startswith(boundary + "/")
            ):
                reject_text_companion(TextCompanionErrorCode.INVALID)
        if len(self._preserved_artifacts) >= MAX_PRESERVED_EXPORT_ARTIFACTS:
            reject_text_companion(TextCompanionErrorCode.LIMIT_EXCEEDED)

    def _remember_optional_output(self, destination: Path, payload: bytes) -> None:
        if self._verified_snapshot is not None:
            self._preserved_artifacts += (
                PreservedExportArtifact(
                    destination, len(payload), hashlib.sha256(payload).hexdigest()
                ),
            )
            self._notify_verified_export()

    @contextmanager
    def _guard_companion_sources(
        self, deadline: Callable[[], bool], cancellation: Callable[[], bool]
    ) -> Iterator[None]:
        if self._forbidden_export_root is None:
            reject_text_companion(TextCompanionErrorCode.EXPORT_REQUIRED)
        try:
            with self.guard_last_verified_export(
                self._forbidden_export_root, deadline=deadline, cancellation=cancellation
            ):
                yield
        except PostExportDeletionError as error:
            reject_text_companion(
                TextCompanionErrorCode.CANCELLED
                if error.code is PostExportDeletionErrorCode.CANCELLED
                else TextCompanionErrorCode.EXPORT_CHANGED
            )

    def save_text_companion(
        self,
        destination: Path,
        *,
        privacy_confirmed: bool,
        deadline: Callable[[], bool],
        cancellation: Callable[[], bool],
        expected_snapshot: ManifestSnapshot | None = None,
    ) -> SummarySaveState:
        """Save opt-in UTF-8 derived solely from the current verified final PDFs."""
        if privacy_confirmed is not True:
            reject_text_companion(TextCompanionErrorCode.CONSENT_REQUIRED)
        if (
            self._verified_snapshot is None
            or self._verified_outcome is None
            or self._verified_parent is None
            or self._text_extractor is None
            or self._forbidden_export_root is None
        ):
            reject_text_companion(TextCompanionErrorCode.EXPORT_REQUIRED)
        if expected_snapshot is not None and (
            type(expected_snapshot) is not ManifestSnapshot
            or expected_snapshot.case_id != self._verified_snapshot.case_id
            or expected_snapshot.revision != self._verified_snapshot.revision
            or expected_snapshot.sha256 != self._verified_snapshot.sha256
            or expected_snapshot.cas_token != self._verified_snapshot.cas_token
        ):
            reject_text_companion(TextCompanionErrorCode.EXPORT_REQUIRED)
        if not isinstance(destination, Path) or destination.suffix.casefold() != ".txt":
            reject_text_companion(TextCompanionErrorCode.INVALID)
        self._require_optional_destination(destination)
        require_companion_active(deadline, cancellation)
        limits = TextCompanionLimits.from_resource_policy(self._policy)
        header = tr(
            "PaperWorkShell text companion\n\nThis separate file contains extractable text from the verified exported PDFs. It may contain sensitive information and remains after case deletion. Review it before sharing. OCR and reading order may be wrong; images and visual marks may have no text. Extracted text can differ from the visible page. Characters outside the visible crop are omitted. This is not a replacement for the PDF or a claim of PDF/UA conformance.\n"
        ).encode("utf-8")
        with BytesIO() as payload:
            payload.write(header)
            outcome, snapshot = (
                self._verified_outcome,
                self._verified_snapshot,
            )
            plan = outcome.report.plan
            with self._guard_companion_sources(deadline, cancellation):
                for position, pdf in enumerate(outcome.report.pdfs):
                    require_companion_active(deadline, cancellation)
                    artifact = self._preserved_artifacts[
                        0 if plan.archive is not None else position
                    ]
                    with open_handle(artifact.path, read=True) as source:
                        content = self._text_extractor.extract_handle(
                            snapshot.case_id,
                            source.value,
                            expected_byte_size=artifact.byte_size,
                            expected_sha256=artifact.sha256,
                            page_count=len(pdf.page_ids),
                            deadline=deadline,
                            cancellation=cancellation,
                            member_position=position if plan.archive is not None else None,
                            member_byte_size=pdf.base_report.output_byte_size
                            if plan.archive is not None
                            else None,
                            member_sha256=pdf.base_report.output_sha256
                            if plan.archive is not None
                            else None,
                        )
                    # Do not repeat private output filenames or source metadata.
                    title = tr("\nPDF {value_0}\n", value_0=position + 1).encode("utf-8")
                    if (
                        type(content) is not bytes
                        or len(content) + len(title) > limits.maximum_output_bytes - payload.tell()
                    ):
                        reject_text_companion(TextCompanionErrorCode.LIMIT_EXCEEDED)
                    try:
                        content.decode("utf-8", errors="strict")
                    except UnicodeError:
                        reject_text_companion(TextCompanionErrorCode.EXTRACTION_FAILED)
                    payload.write(title)
                    payload.write(content)
                require_companion_active(deadline, cancellation)
                data = payload.getvalue()
                payload.seek(0)
                result = self._destination.commit(
                    destination,
                    payload,
                    expected_byte_size=len(data),
                    expected_sha256=hashlib.sha256(data).hexdigest(),
                    cancellation=cancellation,
                    deadline=deadline,
                )
            if result.committed:
                self._remember_optional_output(destination, data)
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

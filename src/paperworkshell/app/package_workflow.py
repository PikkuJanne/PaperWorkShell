"""Revision-bound package review; concrete writers remain injected adapters.

Review deliberately does not promote manifest import flags to full inspection or
quality evidence. The core rules report those unavailable checks explicitly.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from pathlib import Path
from time import monotonic
from typing import Never, Protocol, cast
from uuid import UUID, uuid4

from paperworkshell.app.checklist import checklist_definition_from_template
from paperworkshell.app.signed_source_policy import (
    SignedSourceConfirmationChoice,
    SignedSourceConfirmationService,
)
from paperworkshell.app.source_ingest import (
    DEFAULT_SOURCE_IMPORT_TIMEOUT_SECONDS,
    CancellationProbe,
    DeadlineProbe,
)
from paperworkshell.domain.checklist import ChecklistCommandHistory, ChecklistDefinition
from paperworkshell.domain.import_precheck import DetectedMediaType
from paperworkshell.domain.manifest_repository import ManifestRepository, ManifestSnapshot
from paperworkshell.domain.package_summary import PackageSummary
from paperworkshell.domain.signed_source_policy import (
    SignedSourceModificationReview,
    SignedSourceModificationSource,
    SignedSourceSignatureState,
    build_signed_source_modification_review,
)
from paperworkshell.preflight.core_rules import core_preflight_rules
from paperworkshell.preflight.engine import PreflightEngine, PreflightEvaluation
from paperworkshell.preflight.facts import (
    CasePreflightFacts,
    CaseSourcePreflightFact,
    ChecklistFactAvailability,
    ChecklistPreflightFacts,
    OutputPlanAvailability,
    OutputPlanPreflightFacts,
    QualityFactAvailability,
    QualityPreflightFacts,
    SourceInspectionAvailability,
    SourceInspectionFact,
    SourceInspectionsPreflightFacts,
    TemplateFactAvailability,
    TemplateNoticeFact,
    TemplateNoticeKind,
    TemplatePreflightFacts,
)
from paperworkshell.preflight.models import (
    PreflightAuthority,
    PreflightDependency,
    PreflightFactValue,
    PreflightInput,
    PreflightScope,
    PreflightScopeCatalog,
    PreflightScopeKind,
)
from paperworkshell.preflight.package_planner import (
    PackageExportPlan,
    PackagePlannerError,
    PackagePlannerErrorCode,
    build_package_export_plan,
)
from paperworkshell.preflight.portal_constraints import (
    PortalOutputMode,
    portal_constraint_profile_from_template,
)
from paperworkshell.templates.loader import SafeTemplateLoader
from paperworkshell.templates.models import TemplateKey, TemplateSnapshot


class PackageWorkflowErrorCode(StrEnum):
    UNAVAILABLE = "PACKAGE_WORKFLOW_UNAVAILABLE"
    ACTIVE = "PACKAGE_WORKFLOW_ACTIVE"
    STALE = "PACKAGE_WORKFLOW_STALE"
    REVIEW_REQUIRED = "PACKAGE_WORKFLOW_REVIEW_REQUIRED"
    BLOCKED = "PACKAGE_WORKFLOW_BLOCKED"
    DESTINATION_CHANGED = "PACKAGE_WORKFLOW_DESTINATION_CHANGED"
    SAVE_FAILED = "PACKAGE_WORKFLOW_SAVE_FAILED"
    CANCELLED = "PACKAGE_WORKFLOW_CANCELLED"
    INVALID = "PACKAGE_WORKFLOW_INVALID"
    FILENAME_MODE_MISMATCH = "PACKAGE_WORKFLOW_FILENAME_MODE_MISMATCH"


_MESSAGES = {
    PackageWorkflowErrorCode.UNAVAILABLE: "Package export is not available in this session.",
    PackageWorkflowErrorCode.ACTIVE: "Close the package review before changing the case.",
    PackageWorkflowErrorCode.STALE: "The case changed. Close package review and open it again.",
    PackageWorkflowErrorCode.REVIEW_REQUIRED: (
        "Review the package warnings and required confirmations before exporting."
    ),
    PackageWorkflowErrorCode.BLOCKED: (
        "The package cannot be exported. Close this review and resolve the missing items or errors."
    ),
    PackageWorkflowErrorCode.DESTINATION_CHANGED: (
        "This destination would change the reviewed filenames. Choose a shorter destination path."
    ),
    PackageWorkflowErrorCode.SAVE_FAILED: (
        "The summary was not confirmed saved. Check the destination and choose a new filename."
    ),
    PackageWorkflowErrorCode.CANCELLED: "Package export was cancelled before writing files.",
    PackageWorkflowErrorCode.INVALID: "PaperWorkShell received invalid package review data.",
    PackageWorkflowErrorCode.FILENAME_MODE_MISMATCH: (
        "The saved filenames do not match this output organization. Close package review, "
        "open Constraints, and review the filenames for the output organization you want."
    ),
}


@dataclass(slots=True, repr=False)
class PackageWorkflowError(ValueError):
    code: PackageWorkflowErrorCode
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not PackageWorkflowErrorCode:
            self.code = PackageWorkflowErrorCode.INVALID
        self.safe_message = _MESSAGES[self.code]
        self.args = (self.code.value, self.safe_message)

    def __repr__(self) -> str:
        return f"PackageWorkflowError(code={self.code.value!r})"


def reject_package_workflow(code: PackageWorkflowErrorCode) -> Never:
    error = PackageWorkflowError(code)
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None


@dataclass(frozen=True, slots=True, repr=False)
class PackageReviewState:
    review_id: UUID
    summary: PackageSummary = field(repr=False)
    allowed_modes: tuple[PortalOutputMode, ...]
    selected_mode: PortalOutputMode
    export_allowed: bool
    signed_source_confirmation_required: bool
    human_review_required: bool = True

    def __post_init__(self) -> None:
        if (
            type(self.review_id) is not UUID
            or self.review_id.version != 4
            or type(self.summary) is not PackageSummary
            or type(self.allowed_modes) is not tuple
            or not self.allowed_modes
            or any(type(mode) is not PortalOutputMode for mode in self.allowed_modes)
            or len(set(self.allowed_modes)) != len(self.allowed_modes)
            or type(self.selected_mode) is not PortalOutputMode
            or self.selected_mode not in self.allowed_modes
            or any(
                type(value) is not bool
                for value in (
                    self.export_allowed,
                    self.signed_source_confirmation_required,
                    self.human_review_required,
                )
            )
        ):
            reject_package_workflow(PackageWorkflowErrorCode.INVALID)

    def __repr__(self) -> str:
        return f"PackageReviewState(export_allowed={self.export_allowed!r})"


class PackageSummaryBuilder(Protocol):
    def __call__(
        self,
        snapshot: ManifestSnapshot,
        template: TemplateSnapshot,
        definition: ChecklistDefinition,
        preflight: PreflightEvaluation,
        *,
        plan: PackageExportPlan | None = None,
    ) -> PackageSummary: ...


class PackageExportPort(Protocol):
    def export(
        self,
        snapshot: ManifestSnapshot,
        template: TemplateSnapshot,
        definition: ChecklistDefinition,
        preflight: PreflightEvaluation,
        plan: PackageExportPlan,
        destination_parent: Path,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: Callable[[int, int], None],
        human_review_confirmed: bool = False,
    ) -> PackageSummary: ...

    def save_summary(
        self,
        summary: PackageSummary,
        destination: Path,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> str: ...


@dataclass(slots=True, repr=False)
class _Review:
    snapshot: ManifestSnapshot
    template: TemplateSnapshot
    definition: ChecklistDefinition
    evaluation: PreflightEvaluation
    plan: PackageExportPlan | None
    signature: SignedSourceModificationReview
    state: PackageReviewState


class PackageWorkflowService:
    """One exclusive, transient review with exact saved-authority revalidation."""

    def __init__(
        self,
        repository: ManifestRepository,
        loader: SafeTemplateLoader,
        template_key: TemplateKey,
        *,
        as_of: date,
        exporter: PackageExportPort,
        summary_builder: PackageSummaryBuilder,
    ) -> None:
        self._repository = repository
        self._loader = loader
        self._key = template_key
        self._as_of = as_of
        self._exporter = exporter
        self._builder = summary_builder
        self._active: _Review | None = None
        self._summary: PackageSummary | None = None

    @property
    def active(self) -> bool:
        return self._active is not None

    @property
    def current_snapshot(self) -> ManifestSnapshot | None:
        """Backend-only authority, including any durable signed-source consent."""
        return None if self._active is None else self._active.snapshot

    def begin(
        self,
        snapshot: ManifestSnapshot,
        mode: PortalOutputMode | None = None,
    ) -> PackageReviewState:
        if self.active:
            reject_package_workflow(PackageWorkflowErrorCode.ACTIVE)
        self._summary = None
        self._active = self._review(snapshot, mode)
        return self._active.state

    def refresh(self, mode: PortalOutputMode) -> PackageReviewState:
        review = self._require_current()
        replacement = self._review(review.snapshot, mode)
        self._active = replacement
        self._summary = None
        return replacement.state

    def cancel(self) -> None:
        self._active = None
        self._summary = None

    def export(
        self,
        destination_parent: Path,
        *,
        review_id: UUID,
        signed_source_confirmed: bool,
        human_review_confirmed: bool,
        cancellation: CancellationProbe | None = None,
        progress: Callable[[int, int], None] | None = None,
        snapshot_changed: Callable[[ManifestSnapshot], None] | None = None,
    ) -> PackageSummary:
        review = self._require_current()
        if type(review_id) is not UUID or review_id != review.state.review_id:
            reject_package_workflow(PackageWorkflowErrorCode.STALE)
        if self._summary is not None:
            reject_package_workflow(PackageWorkflowErrorCode.REVIEW_REQUIRED)
        if not review.state.export_allowed or review.plan is None:
            reject_package_workflow(PackageWorkflowErrorCode.BLOCKED)
        if human_review_confirmed is not True or (
            review.state.signed_source_confirmation_required and signed_source_confirmed is not True
        ):
            reject_package_workflow(PackageWorkflowErrorCode.REVIEW_REQUIRED)
        probe = cancellation if cancellation is not None else lambda: False
        if probe():
            reject_package_workflow(PackageWorkflowErrorCode.CANCELLED)
        if not isinstance(destination_parent, Path) or not destination_parent.is_absolute():
            reject_package_workflow(PackageWorkflowErrorCode.INVALID)
        actual_plan = self._plan(review, destination_parent)
        if actual_plan != review.plan:
            reject_package_workflow(PackageWorkflowErrorCode.DESTINATION_CHANGED)
        if review.state.signed_source_confirmation_required:
            _outcome, saved = SignedSourceConfirmationService(
                self._repository
            ).decide_with_snapshot(
                review.signature,
                SignedSourceConfirmationChoice.CREATE_UNSIGNED_DERIVATIVE,
            )
            # Keep the exact CAS result even if re-evaluation or desktop refresh
            # fails. Never adopt a later unrelated repository revision as consent.
            review.snapshot = saved
            if snapshot_changed is not None:
                snapshot_changed(saved)
            review = self._review(saved, review.state.selected_mode)
            self._active = review
            actual_plan = self._plan(review, destination_parent)
        self._require_current()
        if not review.state.export_allowed:
            reject_package_workflow(PackageWorkflowErrorCode.BLOCKED)
        expires = monotonic() + DEFAULT_SOURCE_IMPORT_TIMEOUT_SECONDS
        summary = self._exporter.export(
            review.snapshot,
            review.template,
            review.definition,
            review.evaluation,
            actual_plan,
            destination_parent,
            deadline=lambda: monotonic() >= expires,
            cancellation=probe,
            progress=progress if progress is not None else lambda _a, _b: None,
            human_review_confirmed=True,
        )
        if type(summary) is not PackageSummary:
            reject_package_workflow(PackageWorkflowErrorCode.INVALID)
        self._summary = summary
        return summary

    def save_summary(
        self,
        destination: Path,
        *,
        cancellation: CancellationProbe | None = None,
    ) -> None:
        if self._active is None or self._summary is None:
            reject_package_workflow(PackageWorkflowErrorCode.REVIEW_REQUIRED)
        expires = monotonic() + DEFAULT_SOURCE_IMPORT_TIMEOUT_SECONDS
        result = self._exporter.save_summary(
            self._summary,
            destination,
            deadline=lambda: monotonic() >= expires,
            cancellation=cancellation if cancellation is not None else lambda: False,
        )
        if result != "committed":
            reject_package_workflow(PackageWorkflowErrorCode.SAVE_FAILED)

    def _plan(self, review: _Review, parent: Path) -> PackageExportPlan:
        return build_package_export_plan(
            review.snapshot,
            review.definition,
            portal_constraint_profile_from_template(review.template),
            review.state.selected_mode,
            destination_parent_utf16_units=len(str(parent).encode("utf-16-le")) // 2,
        )

    def _require_current(self) -> _Review:
        review = self._active
        if review is None:
            reject_package_workflow(PackageWorkflowErrorCode.REVIEW_REQUIRED)
        current = self._repository.load(review.snapshot.case_id).snapshot
        if (current.revision, current.sha256, current.cas_token) != (
            review.snapshot.revision,
            review.snapshot.sha256,
            review.snapshot.cas_token,
        ):
            reject_package_workflow(PackageWorkflowErrorCode.STALE)
        return review

    def _review(
        self,
        snapshot: ManifestSnapshot,
        mode: PortalOutputMode | None,
    ) -> _Review:
        template = self._loader.load(self._key, as_of=self._as_of)
        definition = checklist_definition_from_template(template)
        profile = portal_constraint_profile_from_template(template)
        constraints = cast(dict[str, object], snapshot.document["constraints"])
        modes = tuple(
            PortalOutputMode(item)
            for item in cast(
                list[str],
                constraints["allowed_output_modes"],
            )
        )
        selected = (
            (
                profile.constraints.default_mode
                if profile.constraints.default_mode in modes
                else modes[0]
            )
            if mode is None
            else mode
        )
        if type(selected) is not PortalOutputMode or selected not in modes:
            reject_package_workflow(PackageWorkflowErrorCode.INVALID)
        try:
            plan = build_package_export_plan(snapshot, definition, profile, selected)
        except PackagePlannerError as error:
            if error.code is PackagePlannerErrorCode.FILENAME_COUNT_INVALID:
                reject_package_workflow(PackageWorkflowErrorCode.FILENAME_MODE_MISMATCH)
            if error.code not in {
                PackagePlannerErrorCode.REQUIREMENT_MISSING,
                PackagePlannerErrorCode.REQUIREMENT_EXCESSIVE,
                PackagePlannerErrorCode.ASSIGNMENT_UNCONFIRMED,
                PackagePlannerErrorCode.PAGE_INACTIVE,
                PackagePlannerErrorCode.PAGE_UNASSIGNED,
                PackagePlannerErrorCode.PAGE_DUPLICATE,
                PackagePlannerErrorCode.PLAN_EMPTY,
            }:
                raise
            plan = None
        evaluation = evaluate_package_review(snapshot, template, definition, selected, plan)
        signature = build_signed_source_modification_review(snapshot, _signature_sources(snapshot))
        state = PackageReviewState(
            uuid4(),
            self._builder(snapshot, template, definition, evaluation, plan=plan),
            modes,
            selected,
            plan is not None and not evaluation.report.has_blocking_errors,
            bool(signature.pending_source_ids),
        )
        return _Review(snapshot, template, definition, evaluation, plan, signature, state)


def _signature_sources(snapshot: ManifestSnapshot) -> tuple[SignedSourceModificationSource, ...]:
    document = snapshot.document
    page_ids = set(cast(list[str], document["page_sequence"]))
    active = {
        cast(str, item["source_id"])
        for item in cast(list[dict[str, object]], document["pages"])
        if item["page_id"] in page_ids
    }
    result: list[SignedSourceModificationSource] = []
    for source in cast(list[dict[str, object]], document["sources"]):
        if source["source_id"] not in active:
            continue
        raw_state = source["signature_state"]
        state = SignedSourceSignatureState(
            "unknown" if raw_state == "not_checked" else cast(str, raw_state)
        )
        result.append(SignedSourceModificationSource(UUID(source["source_id"]), state))
    return tuple(result)


def evaluate_package_review(
    snapshot: ManifestSnapshot,
    template: TemplateSnapshot,
    definition: ChecklistDefinition,
    mode: PortalOutputMode,
    plan: PackageExportPlan | None,
) -> PreflightEvaluation:
    """Assemble bounded saved facts, explicitly marking unperformed checks unknown."""
    case_id, revision = snapshot.case_id, snapshot.revision
    history = ChecklistCommandHistory.from_snapshot(snapshot, definition)
    projection = history.projection
    sources = tuple(
        CaseSourcePreflightFact(
            UUID(cast(str, item["source_id"])),
            position,
            DetectedMediaType(cast(str, item["detected_media_type"])),
            cast(str, item["sha256"]),
        )
        for position, item in enumerate(
            cast(list[dict[str, object]], snapshot.document["sources"]), start=1
        )
    )
    output = (
        plan.output_facts
        if plan is not None
        else OutputPlanPreflightFacts(
            case_id,
            revision,
            OutputPlanAvailability.UNAVAILABLE,
            mode.value,
            (),
        )
    )
    assignments = tuple(item for row in projection.requirements for item in row.assignments)
    scopes = [PreflightScope(PreflightScopeKind.CASE, case_id)]
    inventories: tuple[tuple[PreflightScopeKind, tuple[UUID | str, ...]], ...] = (
        (
            PreflightScopeKind.REQUIREMENT,
            tuple(item.requirement_id for item in definition.requirements),
        ),
        (PreflightScopeKind.SOURCE, tuple(item.source_id for item in sources)),
        (PreflightScopeKind.ASSIGNMENT, tuple(item.assignment_id for item in assignments)),
        (PreflightScopeKind.PAGE, projection.active_page_ids),
        (PreflightScopeKind.OUTPUT, tuple(item.output_key for item in output.items)),
    )
    for kind, identifiers in inventories:
        scopes.extend(
            PreflightScope(kind, identifier, index)
            for index, identifier in enumerate(identifiers, start=1)
        )
    authority = PreflightAuthority(
        case_id,
        revision,
        snapshot.sha256,
        definition.binding,
        PreflightScopeCatalog(case_id, tuple(scopes)),
    )
    document = template.document
    notices: list[TemplateNoticeFact] = []
    for key, id_key, text_key in (
        ("warnings", "warning_id", "message"),
        ("confirmations", "confirmation_id", "statement"),
    ):
        for item in cast(list[dict[str, object]], document[key]):
            scope = cast(dict[str, object], item["scope"])
            notices.append(
                TemplateNoticeFact(
                    cast(str, item[id_key]),
                    TemplateNoticeKind.CONFIRMATION
                    if key == "confirmations"
                    else TemplateNoticeKind(cast(str, item["severity"])),
                    len(notices) + 1,
                    cast(str, item[text_key]),
                    cast(str | None, scope.get("requirement_id")),
                )
            )
    facts: tuple[tuple[PreflightDependency, PreflightFactValue], ...] = (
        (
            PreflightDependency.CASE,
            CasePreflightFacts(
                case_id, revision, definition.binding, sources, projection.active_page_ids
            ),
        ),
        (
            PreflightDependency.TEMPLATE,
            TemplatePreflightFacts(
                case_id,
                revision,
                TemplateFactAvailability.CURRENT,
                definition.binding,
                tuple(notices),
            ),
        ),
        (
            PreflightDependency.CHECKLIST,
            ChecklistPreflightFacts(
                case_id, revision, ChecklistFactAvailability.CURRENT, definition, projection
            ),
        ),
        (
            PreflightDependency.SOURCE_INSPECTIONS,
            SourceInspectionsPreflightFacts(
                case_id,
                revision,
                tuple(
                    SourceInspectionFact(
                        item.source_id,
                        item.position,
                        item.media_type,
                        SourceInspectionAvailability.UNAVAILABLE
                        if item.media_type is DetectedMediaType.PDF
                        else SourceInspectionAvailability.NOT_APPLICABLE,
                        item.source_sha256,
                    )
                    for item in sources
                ),
            ),
        ),
        (
            PreflightDependency.QUALITY_FINDINGS,
            QualityPreflightFacts(
                case_id,
                revision,
                QualityFactAvailability.UNAVAILABLE,
                projection.active_page_ids,
                (),
                None,
                None,
            ),
        ),
        (PreflightDependency.OUTPUT_PLAN, output),
    )
    return PreflightEngine(core_preflight_rules()).evaluate(
        tuple(PreflightInput(authority, dependency, value) for dependency, value in facts)
    )

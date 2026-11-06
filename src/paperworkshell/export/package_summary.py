"""Build a user-visible package summary from exact saved and verified authority.

No source content is read or rendered here. Exact template titles, explicit
review statements, and output leaf names are visible presentation text; all
machine-finding messages come from the registered product wording.
"""

from __future__ import annotations

from typing import Final, cast

from paperworkshell.domain.checklist import (
    ChecklistCommandHistory,
    ChecklistDefinition,
    ChecklistError,
    ChecklistProjection,
    TemplateBinding,
)
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.package_summary import (
    MAX_SUMMARY_ROWS,
    PackageSummary,
    PackageSummaryCategory,
    PackageSummaryError,
    PackageSummaryErrorCode,
    PackageSummaryOutput,
    PackageSummaryOutputState,
    PackageSummaryReviewItem,
    PackageSummaryReviewKind,
    PackageSummaryStatus,
    reject_package_summary,
    require_summary_line,
)
from paperworkshell.domain.pdf_export import PdfExportCheckCode
from paperworkshell.export.package_export import (
    PackageConstraintCheckCode,
    PackageExportOutcome,
)
from paperworkshell.localization.catalog import mark, tr
from paperworkshell.preflight.core_rules import core_preflight_rules
from paperworkshell.preflight.engine import PreflightEvaluation
from paperworkshell.preflight.models import (
    PreflightDependency,
    PreflightError,
    PreflightFindingDefinition,
    PreflightInput,
    PreflightScope,
    PreflightScopeKind,
    PreflightSeverity,
)
from paperworkshell.preflight.package_planner import (
    PackageExportPlan,
    PackagePlannerError,
    build_package_export_plan,
)
from paperworkshell.preflight.portal_constraints import (
    PORTAL_CONSTRAINT_FINDING_DEFINITIONS,
    portal_constraint_profile_from_template,
)
from paperworkshell.storage.export_destination import ExportCommitState
from paperworkshell.templates.models import TemplateSnapshot

_CORE_DEFINITIONS: Final = {
    (rule.descriptor.rule_id, definition.code): definition
    for rule in core_preflight_rules()
    for definition in rule.descriptor.finding_definitions
}
_PORTAL_DEFINITIONS: Final = {
    definition.code: definition for definition in PORTAL_CONSTRAINT_FINDING_DEFINITIONS
}
_CONSTRAINT_CHECK_NAMES: Final = {
    PackageConstraintCheckCode.MODE_ALLOWED: mark(
        "Output organization allowed by saved constraints"
    ),
    PackageConstraintCheckCode.ORGANIZATION_VALID: mark("Output grouping and page inventory"),
    PackageConstraintCheckCode.FILENAMES_VALID: mark("Output filename and path-length rules"),
    PackageConstraintCheckCode.FILE_COUNT_WITHIN_LIMIT: mark("Maximum file count"),
    PackageConstraintCheckCode.TOTAL_SIZE_WITHIN_LIMIT: mark("Maximum total file size"),
    PackageConstraintCheckCode.FILE_SIZE_WITHIN_LIMIT: mark("Maximum individual file size"),
    PackageConstraintCheckCode.PAGE_COUNT_WITHIN_LIMIT: mark("Maximum pages per PDF"),
    PackageConstraintCheckCode.ENCRYPTION_POLICY_SATISFIED: mark("Output encryption policy"),
    PackageConstraintCheckCode.ZIP_ARCHIVE_VERIFIED: mark(
        "Safe ZIP layout, names, order, and member contents"
    ),
}
_OUTPUT_STATES: Final = {
    ExportCommitState.COMMITTED: PackageSummaryOutputState.COMMITTED,
    ExportCommitState.NOT_COMMITTED: PackageSummaryOutputState.NOT_COMMITTED,
    ExportCommitState.DESTINATION_EXISTS: PackageSummaryOutputState.NOT_COMMITTED,
    ExportCommitState.UNKNOWN: PackageSummaryOutputState.UNKNOWN,
}
_REVIEW_KINDS: Final = {
    PreflightSeverity.BLOCKING_ERROR: PackageSummaryReviewKind.BLOCKING_ERROR,
    PreflightSeverity.WARNING: PackageSummaryReviewKind.WARNING,
    PreflightSeverity.CONFIRMATION_REQUIRED: PackageSummaryReviewKind.CONFIRMATION_REQUIRED,
}


def build_package_summary(
    snapshot: ManifestSnapshot,
    template: TemplateSnapshot,
    definition: ChecklistDefinition,
    preflight: PreflightEvaluation,
    *,
    plan: PackageExportPlan | None = None,
    outcome: PackageExportOutcome | None = None,
) -> PackageSummary:
    """Create an exact preview or final result, retaining all human review needs.

    A missing package plan is a supported review state. The returned summary is
    display-only and never authorizes export or acknowledges a finding.
    """
    try:
        return _build_package_summary(snapshot, template, definition, preflight, plan, outcome)
    except PackageSummaryError:
        raise
    except ChecklistError, PreflightError, PackagePlannerError:
        reject_package_summary(PackageSummaryErrorCode.AUTHORITY_MISMATCH)
    except Exception:
        reject_package_summary(PackageSummaryErrorCode.MODEL_INVALID)


def build_package_summary_recovery(outcome: PackageExportOutcome) -> PackageSummary:
    """Preserve known publication facts when detailed presentation fails later.

    This intentionally has no dependency on a template, preflight, or fresh
    case read. It cannot conceal a committed file behind a retryable error.
    The conspicuous review warning prevents this inventory from standing in
    for the unavailable checklist and preflight details.
    """
    if type(outcome) is not PackageExportOutcome:
        reject_package_summary(PackageSummaryErrorCode.MODEL_INVALID)
    _validate_destination(outcome)
    plan = outcome.report.plan
    summary = PackageSummary(
        status=PackageSummaryStatus(outcome.status.value),
        outputs=_outputs(plan, outcome, ChecklistProjection((), (), (), ()), {}),
        active_page_count=sum(len(item.page_ids) for item in plan.pdfs),
        checklist_available=False,
        checks=tuple(
            (tr("Check complete: ") if item.passed else tr("Constraint not met: "))
            + tr(_CONSTRAINT_CHECK_NAMES[item.code])
            + "."
            for item in outcome.report.constraint_checks
        ),
        review_items=(
            PackageSummaryReviewItem(
                PackageSummaryReviewKind.WARNING,
                tr("Detailed package summary unavailable"),
                tr(
                    "File save results are listed, but the detailed checklist and preflight review could not be prepared."
                ),
                tr(
                    "Check the destination inventory before retrying. Reopen the case and review all warnings and checklist details before sharing files."
                ),
            ),
        ),
    )
    summary.to_plain_text()
    return summary


def _build_package_summary(
    snapshot: ManifestSnapshot,
    template: TemplateSnapshot,
    definition: ChecklistDefinition,
    preflight: PreflightEvaluation,
    plan: PackageExportPlan | None,
    outcome: PackageExportOutcome | None,
) -> PackageSummary:
    if (
        type(snapshot) is not ManifestSnapshot
        or type(template) is not TemplateSnapshot
        or type(definition) is not ChecklistDefinition
        or type(preflight) is not PreflightEvaluation
        or (plan is not None and type(plan) is not PackageExportPlan)
        or (outcome is not None and type(outcome) is not PackageExportOutcome)
    ):
        reject_package_summary(PackageSummaryErrorCode.MODEL_INVALID)
    labels = _template_labels(template, definition)
    projection = ChecklistCommandHistory.from_snapshot(snapshot, definition).projection
    authority = preflight.authority
    if (
        authority.case_id != snapshot.case_id
        or authority.manifest_revision != snapshot.revision
        or authority.manifest_sha256 != snapshot.sha256
        or authority.template_binding != definition.binding
        or preflight.report.authority_sha256 != authority.sha256
    ):
        reject_package_summary(PackageSummaryErrorCode.AUTHORITY_MISMATCH)
    if outcome is not None and (plan is None or outcome.report.plan != plan):
        reject_package_summary(PackageSummaryErrorCode.AUTHORITY_MISMATCH)
    if plan is not None:
        expected = build_package_export_plan(
            snapshot, definition, portal_constraint_profile_from_template(template), plan.mode
        )
        output_revision = PreflightInput(
            authority, PreflightDependency.OUTPUT_PLAN, plan.output_facts
        ).revision_sha256
        if (
            plan != expected
            or preflight.dependency_revision(PreflightDependency.OUTPUT_PLAN) != output_revision
        ):
            reject_package_summary(PackageSummaryErrorCode.AUTHORITY_MISMATCH)
        output_scopes = tuple(
            scope
            for scope in authority.scope_catalog.scopes
            if scope.kind is PreflightScopeKind.OUTPUT
        )
        if output_scopes != tuple(
            PreflightScope(PreflightScopeKind.OUTPUT, item.output_key, item.position)
            for item in plan.pdfs
        ):
            reject_package_summary(PackageSummaryErrorCode.AUTHORITY_MISMATCH)
    elif any(scope.kind is PreflightScopeKind.OUTPUT for scope in authority.scope_catalog.scopes):
        reject_package_summary(PackageSummaryErrorCode.AUTHORITY_MISMATCH)
    if outcome is not None:
        _validate_destination(outcome)
    categories = _categories(projection, labels)
    reviews: list[PackageSummaryReviewItem] = []
    if len(preflight.report.findings) > MAX_SUMMARY_ROWS:
        reject_package_summary(PackageSummaryErrorCode.LIMIT_EXCEEDED)
    for finding in preflight.report.findings:
        if not authority.scope_catalog.contains(finding.scope) or (
            finding.related_scope is not None
            and not authority.scope_catalog.contains(finding.related_scope)
        ):
            reject_package_summary(PackageSummaryErrorCode.AUTHORITY_MISMATCH)
        known = _CORE_DEFINITIONS.get((finding.rule_id, finding.code))
        if known is None:
            candidate = _PORTAL_DEFINITIONS.get(finding.code)
            if candidate == finding.definition:
                known = candidate
        if known != finding.definition:
            known = None
        if (
            outcome is not None
            and outcome.report.constraints_satisfied
            and known is not None
            and known == _PORTAL_DEFINITIONS.get(finding.code)
            and finding.code in {"PORTAL_TOTAL_SIZE_EXCEEDED", "PORTAL_FILE_SIZE_EXCEEDED"}
        ):
            # The exact retained/committed bytes supersede this earlier size
            # estimate. Every unrelated finding still needs its usual review.
            continue
        scope_text = _scope_text(finding.scope, labels, plan)
        if finding.related_scope is not None:
            scope_text += tr("; related ") + _scope_text(finding.related_scope, labels, plan)
        reviews.append(_review(finding.severity, scope_text, known))
    reviews.extend(_template_reviews(template, labels))
    if outcome is not None:
        for constraint_finding in outcome.report.constraint_findings:
            known = _PORTAL_DEFINITIONS.get(constraint_finding.code)
            reviews.append(
                _review(
                    PreflightSeverity.BLOCKING_ERROR,
                    _scope_text(constraint_finding.scope, labels, plan),
                    known,
                )
            )
    checks = [
        tr(
            "Preflight evaluated {value_0} local rule groups. Their findings and human review needs are listed below.",
            value_0=len(preflight.report.rule_ids),
        )
    ]
    if outcome is not None:
        checks.extend(
            (tr("Check complete: ") if item.passed else tr("Constraint not met: "))
            + tr(_CONSTRAINT_CHECK_NAMES[item.code])
            + "."
            for item in outcome.report.constraint_checks
        )
    else:
        checks.append(
            tr(
                "Final file sizes, PDF verification, and destination save checks have not been completed."
            )
        )
    status = (
        (
            PackageSummaryStatus.NEEDS_REVIEW
            if plan is None or reviews
            else PackageSummaryStatus.PLANNED
        )
        if outcome is None
        else PackageSummaryStatus(outcome.status.value)
    )
    summary = PackageSummary(
        status=status,
        outputs=_outputs(plan, outcome, projection, labels),
        categories=categories,
        checks=tuple(checks),
        review_items=tuple(reviews),
        active_page_count=len(projection.active_page_ids),
        unassigned_page_count=len(projection.unassigned_page_ids),
        unconfirmed_page_count=len(projection.unconfirmed_page_ids),
    )
    # Prove the complete visible representation fits its bounded contract before
    # exposing a summary to the widget or a user-selected companion-file writer.
    summary.to_plain_text()
    return summary


def _template_labels(template: TemplateSnapshot, definition: ChecklistDefinition) -> dict[str, str]:
    if definition.binding != TemplateBinding(
        template.key.template_id, template.key.template_version, template.sha256
    ):
        reject_package_summary(PackageSummaryErrorCode.AUTHORITY_MISMATCH)
    document = template.document
    requirements = document.get("requirements")
    order = document.get("requirement_order")
    if (
        type(requirements) is not list
        or type(order) is not list
        or order != [item.requirement_id for item in definition.requirements]
    ):
        reject_package_summary(PackageSummaryErrorCode.AUTHORITY_MISMATCH)
    by_id: dict[str, dict[str, object]] = {}
    for value in requirements:
        if type(value) is not dict or type(value.get("requirement_id")) is not str:
            reject_package_summary(PackageSummaryErrorCode.MODEL_INVALID)
        requirement_id = cast(str, value["requirement_id"])
        if requirement_id in by_id:
            reject_package_summary(PackageSummaryErrorCode.MODEL_INVALID)
        by_id[requirement_id] = cast(dict[str, object], value)
    if set(by_id) != set(order):
        reject_package_summary(PackageSummaryErrorCode.AUTHORITY_MISMATCH)
    labels: dict[str, str] = {}
    for rule in definition.requirements:
        value = by_id[rule.requirement_id]
        count = value.get("count")
        if type(count) is not dict or count != {
            "unit": rule.count_unit.value,
            "minimum": rule.minimum_count,
            "maximum": rule.maximum_count,
        }:
            reject_package_summary(PackageSummaryErrorCode.AUTHORITY_MISMATCH)
        labels[rule.requirement_id] = tr(require_summary_line(value.get("title")))
    return labels


def _categories(
    projection: ChecklistProjection, labels: dict[str, str]
) -> tuple[PackageSummaryCategory, ...]:
    return tuple(
        PackageSummaryCategory(
            label=labels[item.requirement.requirement_id],
            status=item.status,
            assignment_count=len(item.assignments),
            page_count=sum(len(value.page_ids) for value in item.assignments),
            confirmed_count=item.confirmed_count,
            count_unit=item.requirement.count_unit,
            minimum_count=item.requirement.minimum_count,
            maximum_count=item.requirement.maximum_count,
        )
        for item in projection.requirements
    )


def _template_reviews(
    template: TemplateSnapshot, labels: dict[str, str]
) -> tuple[PackageSummaryReviewItem, ...]:
    document = template.document
    result: list[PackageSummaryReviewItem] = []
    for key, message_key in (("warnings", "message"), ("confirmations", "statement")):
        values = document.get(key)
        if type(values) is not list:
            reject_package_summary(PackageSummaryErrorCode.MODEL_INVALID)
        if len(values) > MAX_SUMMARY_ROWS:
            reject_package_summary(PackageSummaryErrorCode.LIMIT_EXCEEDED)
        for raw in values:
            if type(raw) is not dict or type(raw.get("scope")) is not dict:
                reject_package_summary(PackageSummaryErrorCode.MODEL_INVALID)
            if key == "warnings" and raw.get("severity") == "information":
                continue
            scope = raw["scope"]
            scope_text = tr("Template guidance for package")
            if scope.get("kind") == "requirement":
                requirement_id = scope.get("requirement_id")
                if type(requirement_id) is not str or requirement_id not in labels:
                    reject_package_summary(PackageSummaryErrorCode.AUTHORITY_MISMATCH)
                scope_text = tr("Template guidance for category ") + labels[requirement_id]
            elif scope.get("kind") != "package":
                reject_package_summary(PackageSummaryErrorCode.MODEL_INVALID)
            message = tr(require_summary_line(raw.get(message_key)))
            result.append(
                PackageSummaryReviewItem(
                    PackageSummaryReviewKind.WARNING
                    if key == "warnings"
                    else PackageSummaryReviewKind.CONFIRMATION_REQUIRED,
                    scope_text,
                    tr("Template statement to review: ") + message,
                    tr(
                        "Review this statement against the pages and the recipient's current instructions."
                    ),
                )
            )
    return tuple(result)


def _scope_text(
    scope: PreflightScope, labels: dict[str, str], plan: PackageExportPlan | None
) -> str:
    if scope.kind is PreflightScopeKind.CASE:
        return tr("Package")
    if scope.kind is PreflightScopeKind.REQUIREMENT:
        label = labels.get(str(scope.item_id))
        if label is None:
            reject_package_summary(PackageSummaryErrorCode.AUTHORITY_MISMATCH)
        return tr("Category ") + label
    if scope.kind is PreflightScopeKind.OUTPUT and plan is not None:
        if plan.archive is not None and scope.item_id == plan.archive.output_key:
            return tr("ZIP file ") + plan.archive.filename
        for pdf in plan.pdfs:
            if pdf.output_key == scope.item_id:
                return tr("PDF ") + pdf.filename
        reject_package_summary(PackageSummaryErrorCode.AUTHORITY_MISMATCH)
    label = {
        PreflightScopeKind.SOURCE: tr("Imported document"),
        PreflightScopeKind.ASSIGNMENT: tr("Checklist document"),
        PreflightScopeKind.PAGE: tr("Page"),
        PreflightScopeKind.OUTPUT: tr("Output"),
    }[scope.kind]
    return label + (
        " " + str(scope.position) if scope.position is not None else tr(" requiring review")
    )


def _review(
    severity: PreflightSeverity, scope: str, definition: PreflightFindingDefinition | None
) -> PackageSummaryReviewItem:
    return PackageSummaryReviewItem(
        _REVIEW_KINDS[severity],
        scope,
        tr("An additional local check requires review.")
        if definition is None
        else tr(definition.message),
        tr("Return to Check and review the finding before export.")
        if definition is None
        else tr(definition.remediation),
    )


def _validate_destination(outcome: PackageExportOutcome) -> None:
    if outcome.destination is None:
        return
    plan = outcome.report.plan
    expected = (
        (
            (
                plan.archive.output_key,
                outcome.report.archive.archive_byte_size,
                outcome.report.archive.archive_sha256,
            ),
        )
        if plan.archive is not None and outcome.report.archive is not None
        else tuple(
            (item.output_key, item.base_report.output_byte_size, item.base_report.output_sha256)
            for item in outcome.report.pdfs
        )
    )
    actual = outcome.destination.artifacts
    if len(actual) != len(expected):
        reject_package_summary(PackageSummaryErrorCode.AUTHORITY_MISMATCH)
    for artifact, (key, byte_size, digest) in zip(actual, expected, strict=True):
        if artifact.output_key != key or (
            artifact.state is ExportCommitState.COMMITTED
            and (
                artifact.byte_size != byte_size
                or artifact.sha256 != digest
                or not artifact.destination_reopened
            )
        ):
            reject_package_summary(PackageSummaryErrorCode.AUTHORITY_MISMATCH)


def _outputs(
    plan: PackageExportPlan | None,
    outcome: PackageExportOutcome | None,
    projection: ChecklistProjection,
    labels: dict[str, str],
) -> tuple[PackageSummaryOutput, ...]:
    if plan is None:
        return ()
    outputs: list[PackageSummaryOutput] = []
    states = (
        {}
        if outcome is None or outcome.destination is None
        else {item.output_key: _OUTPUT_STATES[item.state] for item in outcome.destination.artifacts}
    )
    default_state = (
        PackageSummaryOutputState.PLANNED
        if outcome is None
        else PackageSummaryOutputState.NOT_COMMITTED
    )
    report_by_key = (
        {} if outcome is None else {item.output_key: item for item in outcome.report.pdfs}
    )
    if plan.archive is not None:
        archive = None if outcome is None else outcome.report.archive
        outputs.append(
            PackageSummaryOutput(
                filename=plan.archive.filename,
                state=states.get(plan.archive.output_key, default_state),
                page_count=sum(len(item.page_ids) for item in plan.pdfs),
                byte_size=None if archive is None else archive.archive_byte_size,
                categories=tuple(
                    labels[item.requirement.requirement_id]
                    for item in projection.requirements
                    if item.assignments
                ),
                checks=()
                if archive is None
                else (
                    tr("The ZIP reopened with the planned PDF member order and safe leaf names."),
                    tr("Every member's size and content matched its verified PDF candidate."),
                    tr(
                        "The archive layout and fixed metadata passed the supported ZIP profile checks."
                    ),
                ),
            )
        )
    for pdf in plan.pdfs:
        report = report_by_key.get(pdf.output_key)
        state_key = pdf.output_key if plan.archive is None else plan.archive.output_key
        state = states.get(state_key, default_state)
        checks: list[str] = []
        if report is not None:
            for check in report.base_report.checks:
                if check.code is PdfExportCheckCode.UNSIGNED_DERIVATIVE:
                    checks.append(check.claim)
                else:
                    checks.append(tr("Before saving: ") + tr(check.claim))
            if report.base_report.optimization is not None:
                checks.append(tr("Before saving: ") + tr(report.base_report.optimization.claim))
            if state is PackageSummaryOutputState.COMMITTED:
                checks.append(
                    tr("The saved file was reopened and matched the verified bytes.")
                    if plan.archive is None
                    else tr("This PDF's bytes were verified in the reopened ZIP.")
                )
        assigned = set(pdf.assignment_ids)
        pdf_labels = tuple(
            labels[item.requirement.requirement_id]
            for item in projection.requirements
            if any(value.assignment_id in assigned for value in item.assignments)
        )
        outputs.append(
            PackageSummaryOutput(
                filename=pdf.filename,
                state=state,
                page_count=len(pdf.page_ids),
                byte_size=None if report is None else report.base_report.output_byte_size,
                categories=pdf_labels,
                checks=tuple(checks),
                archive_member=plan.archive is not None,
            )
        )
    return tuple(outputs)


__all__ = ("build_package_summary", "build_package_summary_recovery")

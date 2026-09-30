"""Pure non-portal-specific preflight rules for PWS-0405 and PWS-0501.

The rules consume only the canonical PWS-0404 fact projections.  Where those
facts cannot prove absence (notably page dimensions, metadata, overlay
flattening, and permanent removal), the result is explicitly unverified rather
than silently clean.
"""

from __future__ import annotations

from typing import Final

from paperworkshell.domain.checklist import (
    AssignmentReviewState,
    ChecklistDefinition,
    ChecklistProjection,
    ChecklistStatus,
    RequirementCountUnit,
)
from paperworkshell.domain.import_precheck import DetectedMediaType
from paperworkshell.domain.pdf_inspection import (
    PdfFeatureState,
    PdfInspectionResult,
    PdfOpenState,
    PdfStructuralState,
)
from paperworkshell.domain.quality_analysis import QualityFindingCode
from paperworkshell.domain.signed_source_policy import SignedSourceSignatureState
from paperworkshell.preflight.facts import (
    MAX_PREFLIGHT_TEMPLATE_NOTICES,
    CasePageDetailsAvailability,
    CasePreflightFacts,
    ChecklistFactAvailability,
    ChecklistPreflightFacts,
    OutputOverlayFlatteningAvailability,
    OutputPlanAvailability,
    OutputPlanPreflightFacts,
    QualityFactAvailability,
    QualityPreflightFacts,
    SourceInspectionAvailability,
    SourceInspectionsPreflightFacts,
    TemplateFactAvailability,
    TemplateNoticeKind,
    TemplatePreflightFacts,
)
from paperworkshell.preflight.models import (
    PreflightConfirmationRequirement,
    PreflightDependency,
    PreflightErrorCode,
    PreflightEvidence,
    PreflightEvidenceUnit,
    PreflightFindingDefinition,
    PreflightFindingTarget,
    PreflightRemediationAction,
    PreflightScope,
    PreflightScopeKind,
    PreflightSeverity,
    reject_preflight,
)
from paperworkshell.preflight.rules import (
    PreflightRule,
    PreflightRuleContext,
    PreflightRuleDescriptor,
)

MAX_CORE_PAGE_DIMENSION_MILLIPOINTS: Final = 14_400_000


def _finding_definition(
    code: str,
    severity: PreflightSeverity,
    action: PreflightRemediationAction,
    message: str,
    remediation: str,
    *,
    confirmation_code: str | None = None,
) -> PreflightFindingDefinition:
    return PreflightFindingDefinition(
        code=code,
        severity=severity,
        remediation_action=action,
        message=message,
        remediation=remediation,
        confirmation=(
            None
            if confirmation_code is None
            else PreflightConfirmationRequirement(confirmation_code)
        ),
    )


_COMPLETENESS_DESCRIPTOR = PreflightRuleDescriptor(
    rule_id="core.completeness",
    rule_version=1,
    dependencies=(
        PreflightDependency.CHECKLIST,
        PreflightDependency.OUTPUT_PLAN,
        PreflightDependency.TEMPLATE,
    ),
    finding_definitions=(
        _finding_definition(
            "CHECKLIST_ASSIGNMENT_CONFIRMATION_REQUIRED",
            PreflightSeverity.CONFIRMATION_REQUIRED,
            PreflightRemediationAction.CONFIRM_ASSIGNMENT,
            "A document assignment still needs user confirmation.",
            "Review the assigned pages and confirm the assignment before export.",
            confirmation_code="checklist.assignment.review",
        ),
        _finding_definition(
            "CHECKLIST_ASSIGNMENT_EMPTY",
            PreflightSeverity.BLOCKING_ERROR,
            PreflightRemediationAction.ASSIGN_PAGES,
            "A document assignment has no active pages.",
            "Restore or assign at least one active page, or remove the empty assignment.",
        ),
        _finding_definition(
            "CHECKLIST_MAXIMUM_EXCEEDED",
            PreflightSeverity.BLOCKING_ERROR,
            PreflightRemediationAction.CONFIRM_ASSIGNMENT,
            "A checklist requirement exceeds its allowed confirmed count.",
            "Review the requirement and remove or move confirmed assignments until it is within the limit.",
        ),
        _finding_definition(
            "CHECKLIST_MINIMUM_NOT_MET",
            PreflightSeverity.BLOCKING_ERROR,
            PreflightRemediationAction.ASSIGN_PAGES,
            "A checklist requirement does not have enough confirmed documents or pages.",
            "Add and confirm the required documents or pages before export.",
        ),
        _finding_definition(
            "CHECKLIST_PAGE_UNASSIGNED",
            PreflightSeverity.BLOCKING_ERROR,
            PreflightRemediationAction.ASSIGN_PAGES,
            "An active page is not assigned to a checklist document.",
            "Assign the page to the appropriate checklist requirement before export.",
        ),
        _finding_definition(
            "CHECKLIST_REQUIREMENT_NEEDS_REVIEW",
            PreflightSeverity.CONFIRMATION_REQUIRED,
            PreflightRemediationAction.CONFIRM_ASSIGNMENT,
            "A checklist requirement has assignments that need review.",
            "Resolve inactive, overlapping, automated, or over-limit assignments and confirm the result.",
            confirmation_code="checklist.requirement.review",
        ),
        _finding_definition(
            "CHECKLIST_UNAVAILABLE",
            PreflightSeverity.BLOCKING_ERROR,
            PreflightRemediationAction.RERUN_PREFLIGHT,
            "The current checklist could not be evaluated.",
            "Refresh the case checklist and run preflight again.",
        ),
        _finding_definition(
            "OUTPUT_PLAN_UNAVAILABLE",
            PreflightSeverity.BLOCKING_ERROR,
            PreflightRemediationAction.REVIEW_OUTPUT_PLAN,
            "The current output plan could not be evaluated.",
            "Review or rebuild the output plan, then run preflight again.",
        ),
        _finding_definition(
            "TEMPLATE_DECLARATIONS_UNAVAILABLE",
            PreflightSeverity.BLOCKING_ERROR,
            PreflightRemediationAction.RERUN_PREFLIGHT,
            "The selected template declarations could not be evaluated.",
            "Reload the selected template and run preflight again.",
        ),
        _finding_definition(
            "TEMPLATE_NOT_SELECTED",
            PreflightSeverity.BLOCKING_ERROR,
            PreflightRemediationAction.REVIEW_CASE,
            "No checklist template is selected for this case.",
            "Select a template before reviewing packet completeness.",
        ),
    ),
)


class CoreCompletenessRule:
    """Evaluate selected-template, checklist, and output-plan completeness."""

    __slots__ = ()

    @property
    def descriptor(self) -> PreflightRuleDescriptor:
        return _COMPLETENESS_DESCRIPTOR

    def evaluate(
        self,
        context: PreflightRuleContext,
        /,
    ) -> tuple[PreflightFindingTarget, ...]:
        template = context.require(PreflightDependency.TEMPLATE, TemplatePreflightFacts)
        checklist = context.require(PreflightDependency.CHECKLIST, ChecklistPreflightFacts)
        output = context.require(PreflightDependency.OUTPUT_PLAN, OutputPlanPreflightFacts)
        case_scope = PreflightScope(PreflightScopeKind.CASE, template.case_id)
        findings: list[PreflightFindingTarget] = []

        if template.availability is TemplateFactAvailability.NOT_SELECTED:
            findings.append(PreflightFindingTarget("TEMPLATE_NOT_SELECTED", case_scope))
        elif template.availability is TemplateFactAvailability.UNAVAILABLE:
            findings.append(PreflightFindingTarget("TEMPLATE_DECLARATIONS_UNAVAILABLE", case_scope))

        if checklist.availability is ChecklistFactAvailability.UNAVAILABLE:
            findings.append(PreflightFindingTarget("CHECKLIST_UNAVAILABLE", case_scope))
        else:
            definition = checklist.definition
            projection = checklist.projection
            if (
                type(definition) is not ChecklistDefinition
                or type(projection) is not ChecklistProjection
            ):
                reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.core_checklist")
            findings.extend(_checklist_findings(projection))

        if output.availability is OutputPlanAvailability.UNAVAILABLE:
            findings.append(PreflightFindingTarget("OUTPUT_PLAN_UNAVAILABLE", case_scope))

        return tuple(findings)


def _checklist_findings(
    projection: ChecklistProjection,
) -> tuple[PreflightFindingTarget, ...]:
    findings: list[PreflightFindingTarget] = []
    active_pages = frozenset(projection.active_page_ids)
    page_positions = {
        page_id: position for position, page_id in enumerate(projection.active_page_ids, start=1)
    }
    assignment_position = 0
    for requirement_position, requirement_projection in enumerate(
        projection.requirements,
        start=1,
    ):
        requirement = requirement_projection.requirement
        requirement_scope = PreflightScope(
            PreflightScopeKind.REQUIREMENT,
            requirement.requirement_id,
            requirement_position,
        )
        count_unit = (
            PreflightEvidenceUnit.DOCUMENTS
            if requirement.count_unit is RequirementCountUnit.DOCUMENTS
            else PreflightEvidenceUnit.PAGES
        )
        count_metric = (
            "confirmed_documents"
            if requirement.count_unit is RequirementCountUnit.DOCUMENTS
            else "confirmed_pages"
        )
        if requirement_projection.confirmed_count < requirement.minimum_count:
            findings.append(
                PreflightFindingTarget(
                    "CHECKLIST_MINIMUM_NOT_MET",
                    requirement_scope,
                    evidence=(
                        PreflightEvidence(
                            count_metric,
                            requirement_projection.confirmed_count,
                            count_unit,
                            requirement.minimum_count,
                        ),
                    ),
                )
            )
        if requirement_projection.confirmed_count > requirement.maximum_count:
            findings.append(
                PreflightFindingTarget(
                    "CHECKLIST_MAXIMUM_EXCEEDED",
                    requirement_scope,
                    evidence=(
                        PreflightEvidence(
                            count_metric,
                            requirement_projection.confirmed_count,
                            count_unit,
                            requirement.maximum_count,
                        ),
                    ),
                )
            )
        if requirement_projection.status is ChecklistStatus.NEEDS_REVIEW:
            findings.append(
                PreflightFindingTarget("CHECKLIST_REQUIREMENT_NEEDS_REVIEW", requirement_scope)
            )
        for assignment in requirement_projection.assignments:
            assignment_position += 1
            assignment_scope = PreflightScope(
                PreflightScopeKind.ASSIGNMENT,
                assignment.assignment_id,
                assignment_position,
            )
            if active_pages.isdisjoint(assignment.page_ids):
                findings.append(
                    PreflightFindingTarget("CHECKLIST_ASSIGNMENT_EMPTY", assignment_scope)
                )
            if assignment.review_state is AssignmentReviewState.SUGGESTED:
                findings.append(
                    PreflightFindingTarget(
                        "CHECKLIST_ASSIGNMENT_CONFIRMATION_REQUIRED",
                        assignment_scope,
                    )
                )

    for page_id in projection.unassigned_page_ids:
        position = page_positions[page_id]
        findings.append(
            PreflightFindingTarget(
                "CHECKLIST_PAGE_UNASSIGNED",
                PreflightScope(PreflightScopeKind.PAGE, page_id, position),
            )
        )
    return tuple(findings)


def _template_notice_code(kind: TemplateNoticeKind, position: int) -> str:
    category = (
        "CONFIRMATION_REQUIRED" if kind is TemplateNoticeKind.CONFIRMATION else "NEEDS_REVIEW"
    )
    return f"TEMPLATE_STATEMENT_{category}_{position:04d}"


def _template_notice_definition(
    kind: TemplateNoticeKind,
    position: int,
) -> PreflightFindingDefinition:
    if kind is TemplateNoticeKind.CONFIRMATION:
        return _finding_definition(
            _template_notice_code(kind, position),
            PreflightSeverity.CONFIRMATION_REQUIRED,
            PreflightRemediationAction.REVIEW_TEMPLATE_STATEMENT,
            "A template statement requires user review and confirmation.",
            "Read the template statement and record the requested confirmation.",
            confirmation_code="template.statement.review",
        )
    return _finding_definition(
        _template_notice_code(kind, position),
        PreflightSeverity.WARNING,
        PreflightRemediationAction.REVIEW_TEMPLATE_STATEMENT,
        "A template statement needs review.",
        "Read the template statement before export.",
    )


_TEMPLATE_NOTICE_DESCRIPTOR = PreflightRuleDescriptor(
    rule_id="core.template-notices",
    rule_version=1,
    dependencies=(PreflightDependency.CHECKLIST, PreflightDependency.TEMPLATE),
    finding_definitions=tuple(
        _template_notice_definition(kind, position)
        for position in range(1, MAX_PREFLIGHT_TEMPLATE_NOTICES + 1)
        for kind in (TemplateNoticeKind.NEEDS_REVIEW, TemplateNoticeKind.CONFIRMATION)
    ),
)


class CoreTemplateNoticeRule:
    """Project each declarative notice with a stable position-specific identity."""

    __slots__ = ()

    @property
    def descriptor(self) -> PreflightRuleDescriptor:
        return _TEMPLATE_NOTICE_DESCRIPTOR

    def evaluate(
        self,
        context: PreflightRuleContext,
        /,
    ) -> tuple[PreflightFindingTarget, ...]:
        template = context.require(PreflightDependency.TEMPLATE, TemplatePreflightFacts)
        checklist = context.require(PreflightDependency.CHECKLIST, ChecklistPreflightFacts)
        definition = (
            checklist.definition
            if checklist.availability is ChecklistFactAvailability.CURRENT
            else None
        )
        if definition is not None and type(definition) is not ChecklistDefinition:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.core_notices")
        return _template_notice_findings(template, definition)


def _template_notice_findings(
    template: TemplatePreflightFacts,
    definition: ChecklistDefinition | None,
) -> tuple[PreflightFindingTarget, ...]:
    requirement_positions = (
        {}
        if definition is None
        else {
            requirement.requirement_id: position
            for position, requirement in enumerate(definition.requirements, start=1)
        }
    )
    case_scope = PreflightScope(PreflightScopeKind.CASE, template.case_id)
    findings: list[PreflightFindingTarget] = []
    for notice in template.notices:
        if notice.kind is TemplateNoticeKind.INFORMATION:
            continue
        position = (
            None
            if notice.requirement_id is None
            else requirement_positions.get(notice.requirement_id)
        )
        scope = (
            case_scope
            if position is None or notice.requirement_id is None
            else PreflightScope(
                PreflightScopeKind.REQUIREMENT,
                notice.requirement_id,
                position,
            )
        )
        code = _template_notice_code(notice.kind, notice.position)
        findings.append(PreflightFindingTarget(code, scope))
    return tuple(findings)


_COMPATIBILITY_DESCRIPTOR = PreflightRuleDescriptor(
    rule_id="core.compatibility",
    rule_version=2,
    dependencies=(PreflightDependency.SOURCE_INSPECTIONS,),
    finding_definitions=(
        _finding_definition(
            "PDF_ACTIONS_UNSUPPORTED",
            PreflightSeverity.BLOCKING_ERROR,
            PreflightRemediationAction.INSPECT_OR_REPLACE_SOURCE,
            "A PDF contains document actions that are unsupported for editing.",
            "Use the unchanged original externally or replace it with a non-interactive PDF.",
        ),
        _finding_definition(
            "PDF_ANNOTATIONS_DETECTED",
            PreflightSeverity.WARNING,
            PreflightRemediationAction.INSPECT_OR_REPLACE_SOURCE,
            "A PDF contains annotations or comment markers that need privacy review.",
            "Review the unchanged original and any derivative before sharing; inspection does not remove annotations or comments.",
        ),
        _finding_definition(
            "PDF_ANNOTATIONS_UNVERIFIED",
            PreflightSeverity.WARNING,
            PreflightRemediationAction.INSPECT_OR_REPLACE_SOURCE,
            "PDF annotation and comment markers could not be fully inspected.",
            "Inspect or replace the source; do not treat this unknown state as proof that annotations or comments are absent.",
        ),
        _finding_definition(
            "PDF_ATTACHMENTS_DETECTED",
            PreflightSeverity.WARNING,
            PreflightRemediationAction.INSPECT_OR_REPLACE_SOURCE,
            "A PDF contains embedded attachments that need privacy review.",
            "Review the unchanged original and confirm whether embedded files belong in the packet.",
        ),
        _finding_definition(
            "PDF_ENCRYPTED",
            PreflightSeverity.BLOCKING_ERROR,
            PreflightRemediationAction.INSPECT_OR_REPLACE_SOURCE,
            "A PDF is encrypted and could not be fully inspected.",
            "Provide an authorized unencrypted copy, then inspect it again.",
        ),
        _finding_definition(
            "PDF_FEATURES_UNVERIFIED",
            PreflightSeverity.WARNING,
            PreflightRemediationAction.INSPECT_OR_REPLACE_SOURCE,
            "Unsupported PDF features were not verified for this source.",
            "Inspect or replace the source before relying on the absence of interactive features.",
        ),
        _finding_definition(
            "PDF_FORM_FIELDS_UNSUPPORTED",
            PreflightSeverity.BLOCKING_ERROR,
            PreflightRemediationAction.INSPECT_OR_REPLACE_SOURCE,
            "A PDF contains form-field markers that are unsupported for editing.",
            "Use the unchanged original externally or provide a static PDF; inspection does not flatten or remove source form fields.",
        ),
        _finding_definition(
            "PDF_FORM_FIELDS_UNVERIFIED",
            PreflightSeverity.WARNING,
            PreflightRemediationAction.INSPECT_OR_REPLACE_SOURCE,
            "PDF form-field markers could not be fully inspected.",
            "Inspect or replace the source; do not treat this unknown state as proof that form fields are absent.",
        ),
        _finding_definition(
            "PDF_INTERACTIVE_CONTENT_UNSUPPORTED",
            PreflightSeverity.BLOCKING_ERROR,
            PreflightRemediationAction.INSPECT_OR_REPLACE_SOURCE,
            "A PDF contains interactive content that is unsupported for editing.",
            "Use the unchanged original externally or replace it with a non-interactive PDF.",
        ),
        _finding_definition(
            "PDF_METADATA_DETECTED",
            PreflightSeverity.WARNING,
            PreflightRemediationAction.INSPECT_OR_REPLACE_SOURCE,
            "A PDF contains metadata markers that need privacy review.",
            "Review the unchanged original and any derivative before sharing; metadata values were not reported and inspection does not remove them.",
        ),
        _finding_definition(
            "PDF_METADATA_UNVERIFIED",
            PreflightSeverity.WARNING,
            PreflightRemediationAction.INSPECT_OR_REPLACE_SOURCE,
            "PDF metadata markers could not be fully inspected.",
            "Inspect or replace the source; do not treat this unknown state as proof that metadata is absent.",
        ),
        _finding_definition(
            "PDF_SCRIPTS_UNSUPPORTED",
            PreflightSeverity.BLOCKING_ERROR,
            PreflightRemediationAction.INSPECT_OR_REPLACE_SOURCE,
            "A PDF contains scripts that are unsupported for editing.",
            "Use the unchanged original externally or replace it with a script-free PDF.",
        ),
        _finding_definition(
            "PDF_SIGNATURE_MARKERS_UNSIGNED_DERIVATIVE",
            PreflightSeverity.WARNING,
            PreflightRemediationAction.INSPECT_OR_REPLACE_SOURCE,
            "A PDF contains cryptographic signature markers.",
            "Any modified or exported copy is an unsigned derivative; retain the unchanged original when signature status matters.",
        ),
        _finding_definition(
            "PDF_SIGNATURE_STATE_REVIEW_REQUIRED",
            PreflightSeverity.BLOCKING_ERROR,
            PreflightRemediationAction.INSPECT_OR_REPLACE_SOURCE,
            "Current inspection found a signature risk that the saved source record does not cover.",
            "Export is blocked. Use the unchanged original externally or import it into a new case for current inspection and signed-source review; acknowledging warnings does not resolve this mismatch.",
        ),
        _finding_definition(
            "PDF_STRUCTURE_FAILED",
            PreflightSeverity.BLOCKING_ERROR,
            PreflightRemediationAction.INSPECT_OR_REPLACE_SOURCE,
            "A PDF failed structural inspection.",
            "Replace the source or repair it outside PaperWorkShell, then inspect it again.",
        ),
        _finding_definition(
            "PDF_STRUCTURE_WARNINGS",
            PreflightSeverity.WARNING,
            PreflightRemediationAction.INSPECT_OR_REPLACE_SOURCE,
            "A PDF produced structural inspection warnings.",
            "Review the unchanged source and the exported derivative before sharing it.",
        ),
        _finding_definition(
            "PDF_XFA_UNSUPPORTED",
            PreflightSeverity.BLOCKING_ERROR,
            PreflightRemediationAction.INSPECT_OR_REPLACE_SOURCE,
            "A PDF contains XFA content that is unsupported for editing.",
            "Use the unchanged original in an appropriate external application or replace it with a static PDF.",
        ),
        _finding_definition(
            "SOURCE_INSPECTION_UNAVAILABLE",
            PreflightSeverity.WARNING,
            PreflightRemediationAction.RERUN_PREFLIGHT,
            "PDF compatibility and privacy are unverified because inspection is unavailable.",
            "Inspect the source again; do not treat unavailable results as proof that features are absent.",
        ),
    ),
)


class CoreCompatibilityRule:
    """Evaluate conservative PDF structure and feature inspection results."""

    __slots__ = ()

    @property
    def descriptor(self) -> PreflightRuleDescriptor:
        return _COMPATIBILITY_DESCRIPTOR

    def evaluate(
        self,
        context: PreflightRuleContext,
        /,
    ) -> tuple[PreflightFindingTarget, ...]:
        facts = context.require(
            PreflightDependency.SOURCE_INSPECTIONS,
            SourceInspectionsPreflightFacts,
        )
        findings: list[PreflightFindingTarget] = []
        for inspection in facts.inspections:
            if inspection.media_type is not DetectedMediaType.PDF:
                continue
            scope = PreflightScope(
                PreflightScopeKind.SOURCE,
                inspection.source_id,
                inspection.position,
            )
            if inspection.availability is SourceInspectionAvailability.UNAVAILABLE:
                findings.append(PreflightFindingTarget("SOURCE_INSPECTION_UNAVAILABLE", scope))
                continue
            result = inspection.pdf_result
            if type(result) is not PdfInspectionResult:
                reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.core_inspection")
            findings.extend(_pdf_result_findings(result, scope))
            if result.features.signature_markers in {
                PdfFeatureState.DETECTED,
                PdfFeatureState.UNKNOWN,
            } and inspection.saved_signature_state not in {
                SignedSourceSignatureState.MARKERS_DETECTED,
                SignedSourceSignatureState.UNKNOWN,
            }:
                findings.append(
                    PreflightFindingTarget("PDF_SIGNATURE_STATE_REVIEW_REQUIRED", scope)
                )
        return tuple(findings)


def _pdf_result_findings(
    result: PdfInspectionResult,
    scope: PreflightScope,
) -> tuple[PreflightFindingTarget, ...]:
    findings: list[PreflightFindingTarget] = []
    if result.open_state is PdfOpenState.ENCRYPTED:
        findings.append(PreflightFindingTarget("PDF_ENCRYPTED", scope))
    elif result.open_state is PdfOpenState.MALFORMED:
        findings.append(PreflightFindingTarget("PDF_STRUCTURE_FAILED", scope))
    elif result.structural_state is PdfStructuralState.FAILED:
        findings.append(PreflightFindingTarget("PDF_STRUCTURE_FAILED", scope))
    elif result.structural_state is PdfStructuralState.WARNINGS:
        findings.append(PreflightFindingTarget("PDF_STRUCTURE_WARNINGS", scope))

    features = result.features
    if PdfFeatureState.UNKNOWN in features.values:
        findings.append(PreflightFindingTarget("PDF_FEATURES_UNVERIFIED", scope))
    for state, detected_code, unknown_code in (
        (features.metadata, "PDF_METADATA_DETECTED", "PDF_METADATA_UNVERIFIED"),
        (features.annotations, "PDF_ANNOTATIONS_DETECTED", "PDF_ANNOTATIONS_UNVERIFIED"),
        (features.form_fields, "PDF_FORM_FIELDS_UNSUPPORTED", "PDF_FORM_FIELDS_UNVERIFIED"),
    ):
        if state is PdfFeatureState.DETECTED:
            findings.append(PreflightFindingTarget(detected_code, scope))
        elif state is PdfFeatureState.UNKNOWN:
            findings.append(PreflightFindingTarget(unknown_code, scope))
    if features.signature_markers is PdfFeatureState.DETECTED:
        findings.append(PreflightFindingTarget("PDF_SIGNATURE_MARKERS_UNSIGNED_DERIVATIVE", scope))
    if features.attachments is PdfFeatureState.DETECTED:
        findings.append(PreflightFindingTarget("PDF_ATTACHMENTS_DETECTED", scope))
    if features.interactive_content is PdfFeatureState.DETECTED:
        findings.append(PreflightFindingTarget("PDF_INTERACTIVE_CONTENT_UNSUPPORTED", scope))
    if features.actions is PdfFeatureState.DETECTED:
        findings.append(PreflightFindingTarget("PDF_ACTIONS_UNSUPPORTED", scope))
    if features.scripts is PdfFeatureState.DETECTED:
        findings.append(PreflightFindingTarget("PDF_SCRIPTS_UNSUPPORTED", scope))
    if features.xfa is PdfFeatureState.DETECTED:
        findings.append(PreflightFindingTarget("PDF_XFA_UNSUPPORTED", scope))
    return tuple(findings)


_QUALITY_CODE_MAP: dict[QualityFindingCode, str] = {
    QualityFindingCode.LIKELY_LOW_RESOLUTION: "QUALITY_LIKELY_LOW_RESOLUTION",
    QualityFindingCode.LIKELY_BLUR: "QUALITY_LIKELY_BLUR",
    QualityFindingCode.SUSPECTED_CLIPPING: "QUALITY_SUSPECTED_CLIPPING",
    QualityFindingCode.LIKELY_BLANK: "QUALITY_LIKELY_BLANK",
    QualityFindingCode.SUSPECTED_SIDEWAYS_ORIENTATION: ("QUALITY_SUSPECTED_SIDEWAYS_ORIENTATION"),
    QualityFindingCode.SUSPECTED_DUPLICATE: "QUALITY_SUSPECTED_DUPLICATE",
    QualityFindingCode.OCR_FAILED: "QUALITY_OCR_FAILED",
    QualityFindingCode.OCR_LOW_CONFIDENCE: "QUALITY_OCR_LOW_CONFIDENCE",
}

_QUALITY_DESCRIPTOR = PreflightRuleDescriptor(
    rule_id="core.quality",
    rule_version=1,
    dependencies=(PreflightDependency.QUALITY_FINDINGS,),
    finding_definitions=(
        _finding_definition(
            "QUALITY_ANALYSIS_UNAVAILABLE",
            PreflightSeverity.WARNING,
            PreflightRemediationAction.RERUN_PREFLIGHT,
            "Page-quality analysis is unavailable for the current case revision.",
            "Run page-quality analysis again and review the pages before export.",
        ),
        _finding_definition(
            "QUALITY_LIKELY_BLANK",
            PreflightSeverity.WARNING,
            PreflightRemediationAction.REVIEW_PAGE,
            "A page is likely blank and needs review.",
            "Review the page and confirm whether it belongs in the packet.",
        ),
        _finding_definition(
            "QUALITY_LIKELY_BLUR",
            PreflightSeverity.WARNING,
            PreflightRemediationAction.REVIEW_PAGE,
            "A page is likely blurry and needs review.",
            "Zoom in and compare important details with the unchanged source.",
        ),
        _finding_definition(
            "QUALITY_LIKELY_LOW_RESOLUTION",
            PreflightSeverity.WARNING,
            PreflightRemediationAction.REVIEW_PAGE,
            "A page likely has low source resolution and needs review.",
            "Review the page at its intended output size before export.",
        ),
        _finding_definition(
            "QUALITY_OCR_FAILED",
            PreflightSeverity.WARNING,
            PreflightRemediationAction.REVIEW_PAGE,
            "OCR did not complete for a page and the page still needs visual review.",
            "Review the page visually; do not treat missing OCR text as proof that the page is blank.",
        ),
        _finding_definition(
            "QUALITY_OCR_LOW_CONFIDENCE",
            PreflightSeverity.WARNING,
            PreflightRemediationAction.REVIEW_PAGE,
            "OCR confidence is low for a page and needs review.",
            "Compare the page image with any OCR-assisted result before relying on it.",
        ),
        _finding_definition(
            "QUALITY_SUSPECTED_CLIPPING",
            PreflightSeverity.WARNING,
            PreflightRemediationAction.REVIEW_PAGE,
            "A page has suspected clipping and needs review.",
            "Review every page edge for missing information.",
        ),
        _finding_definition(
            "QUALITY_SUSPECTED_DUPLICATE",
            PreflightSeverity.WARNING,
            PreflightRemediationAction.COMPARE_PAGES,
            "Two pages are suspected duplicates and need review.",
            "Compare both pages before removing or keeping either one.",
        ),
        _finding_definition(
            "QUALITY_SUSPECTED_SIDEWAYS_ORIENTATION",
            PreflightSeverity.WARNING,
            PreflightRemediationAction.REVIEW_PAGE,
            "A page has a suspected sideways orientation and needs review.",
            "Review the page orientation and rotate it only if needed.",
        ),
    ),
)


class CoreQualityRule:
    """Project advisory page-quality signals without strengthening certainty."""

    __slots__ = ()

    @property
    def descriptor(self) -> PreflightRuleDescriptor:
        return _QUALITY_DESCRIPTOR

    def evaluate(
        self,
        context: PreflightRuleContext,
        /,
    ) -> tuple[PreflightFindingTarget, ...]:
        facts = context.require(PreflightDependency.QUALITY_FINDINGS, QualityPreflightFacts)
        if facts.availability is QualityFactAvailability.UNAVAILABLE:
            return (
                PreflightFindingTarget(
                    "QUALITY_ANALYSIS_UNAVAILABLE",
                    PreflightScope(PreflightScopeKind.CASE, facts.case_id),
                ),
            )
        findings: list[PreflightFindingTarget] = []
        for finding in facts.findings:
            code = _QUALITY_CODE_MAP.get(finding.code)
            if code is None:
                reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.core_quality")
            scope = PreflightScope(
                PreflightScopeKind.PAGE,
                finding.page_id,
                finding.page_position,
            )
            related_scope = (
                None
                if finding.related_page_id is None
                else PreflightScope(
                    PreflightScopeKind.PAGE,
                    finding.related_page_id,
                    finding.related_page_position,
                )
            )
            findings.append(PreflightFindingTarget(code, scope, related_scope))
        return tuple(findings)


_PRIVACY_DESCRIPTOR = PreflightRuleDescriptor(
    rule_id="core.privacy-boundary",
    rule_version=1,
    dependencies=(PreflightDependency.CASE, PreflightDependency.OUTPUT_PLAN),
    finding_definitions=(
        _finding_definition(
            "CASE_PAGE_DETAILS_UNVERIFIED",
            PreflightSeverity.WARNING,
            PreflightRemediationAction.REVIEW_CASE,
            "Page dimensions and overlay application are unverified in this preflight run.",
            "Refresh page details before relying on geometry or overlay privacy checks.",
        ),
        _finding_definition(
            "PAGE_DIMENSIONS_EXTREME",
            PreflightSeverity.WARNING,
            PreflightRemediationAction.REVIEW_PAGE,
            "A page dimension is above the conservative 14,400-point compatibility review threshold.",
            "Review the page size and intended external-system compatibility before export.",
        ),
        _finding_definition(
            "PAGE_ORDINARY_SHAPES_NOT_PERMANENT_REMOVAL",
            PreflightSeverity.WARNING,
            PreflightRemediationAction.REVIEW_PAGE,
            "A page contains ordinary visual shapes that do not prove permanent removal.",
            "Treat shapes and highlights as visual marks only; inspect the unchanged source when information must be permanently removed.",
        ),
        _finding_definition(
            "PAGE_OVERLAY_FLATTENING_UNVERIFIED",
            PreflightSeverity.WARNING,
            PreflightRemediationAction.REVIEW_PAGE,
            "A page has overlays whose derivative-flattening plan is unverified.",
            "Refresh the output plan and verify that every visible overlay will be flattened before export.",
        ),
        _finding_definition(
            "PAGE_OVERLAYS_NOT_PLANNED_FOR_FLATTENING",
            PreflightSeverity.BLOCKING_ERROR,
            PreflightRemediationAction.REVIEW_PAGE,
            "A page has overlays that are not planned for flattening into the derivative.",
            "Select a flattening export path or remove the overlays before export.",
        ),
        _finding_definition(
            "PAGE_UNAPPLIED_OVERLAYS",
            PreflightSeverity.BLOCKING_ERROR,
            PreflightRemediationAction.REVIEW_PAGE,
            "A page has visual overlays that are not applied to the planned derivative.",
            "Apply or remove every pending overlay before export.",
        ),
    ),
)


class CorePrivacyBoundaryRule:
    """Evaluate page geometry and visual intent without implying redaction."""

    __slots__ = ()

    @property
    def descriptor(self) -> PreflightRuleDescriptor:
        return _PRIVACY_DESCRIPTOR

    def evaluate(
        self,
        context: PreflightRuleContext,
        /,
    ) -> tuple[PreflightFindingTarget, ...]:
        facts = context.require(PreflightDependency.CASE, CasePreflightFacts)
        output = context.require(PreflightDependency.OUTPUT_PLAN, OutputPlanPreflightFacts)
        if facts.page_details_availability is CasePageDetailsAvailability.UNAVAILABLE:
            return (
                PreflightFindingTarget(
                    "CASE_PAGE_DETAILS_UNVERIFIED",
                    PreflightScope(PreflightScopeKind.CASE, facts.case_id),
                ),
            )
        findings: list[PreflightFindingTarget] = []
        for page in facts.page_details:
            scope = PreflightScope(PreflightScopeKind.PAGE, page.page_id, page.position)
            if (
                page.width_millipoints > MAX_CORE_PAGE_DIMENSION_MILLIPOINTS
                or page.height_millipoints > MAX_CORE_PAGE_DIMENSION_MILLIPOINTS
            ):
                findings.append(PreflightFindingTarget("PAGE_DIMENSIONS_EXTREME", scope))
            if page.unapplied_overlay_count > 0:
                findings.append(
                    PreflightFindingTarget(
                        "PAGE_UNAPPLIED_OVERLAYS",
                        scope,
                        evidence=(
                            PreflightEvidence(
                                "unapplied_overlay_count",
                                page.unapplied_overlay_count,
                                PreflightEvidenceUnit.COUNT,
                            ),
                        ),
                    )
                )
            if page.overlay_count > 0:
                if (
                    output.availability is OutputPlanAvailability.UNAVAILABLE
                    or output.overlay_flattening_availability
                    is OutputOverlayFlatteningAvailability.UNAVAILABLE
                ):
                    findings.append(
                        PreflightFindingTarget(
                            "PAGE_OVERLAY_FLATTENING_UNVERIFIED",
                            scope,
                        )
                    )
                elif page.page_id not in output.flattened_overlay_page_ids:
                    findings.append(
                        PreflightFindingTarget(
                            "PAGE_OVERLAYS_NOT_PLANNED_FOR_FLATTENING",
                            scope,
                        )
                    )
            if page.ordinary_shape_count > 0:
                findings.append(
                    PreflightFindingTarget(
                        "PAGE_ORDINARY_SHAPES_NOT_PERMANENT_REMOVAL",
                        scope,
                        evidence=(
                            PreflightEvidence(
                                "ordinary_shape_count",
                                page.ordinary_shape_count,
                                PreflightEvidenceUnit.COUNT,
                            ),
                        ),
                    )
                )
        return tuple(findings)


def core_preflight_rules() -> tuple[PreflightRule, ...]:
    """Return the deterministic non-portal core rule registry."""

    return (
        CoreCompletenessRule(),
        CoreCompatibilityRule(),
        CorePrivacyBoundaryRule(),
        CoreQualityRule(),
        CoreTemplateNoticeRule(),
    )


__all__ = (
    "CoreCompatibilityRule",
    "CoreCompletenessRule",
    "CorePrivacyBoundaryRule",
    "CoreQualityRule",
    "CoreTemplateNoticeRule",
    "MAX_CORE_PAGE_DIMENSION_MILLIPOINTS",
    "core_preflight_rules",
)

"""PWS-0405 matrix for the pure non-portal preflight rule set."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from paperworkshell.domain.checklist import (
    AssignmentOrigin,
    AssignmentReviewState,
    ChecklistAssignment,
    ChecklistDefinition,
    ChecklistProjection,
    ChecklistRequirement,
    ChecklistRequirementProjection,
    ChecklistStatus,
    RequirementCountUnit,
    TemplateBinding,
)
from paperworkshell.domain.import_precheck import DetectedMediaType
from paperworkshell.domain.pdf_inspection import (
    PdfEncryptionState,
    PdfFeatureState,
    PdfFeatureSummary,
    PdfInspectionResult,
    PdfOpenState,
    PdfStructuralState,
)
from paperworkshell.domain.quality_analysis import (
    QualityEvidenceKind,
    QualityFinding,
    QualityFindingCode,
    quality_finding_sort_key,
)
from paperworkshell.domain.signed_source_policy import SignedSourceSignatureState
from paperworkshell.overlays.models import (
    ORDINARY_SHAPE_INTENT,
    OverlayKind,
    RgbaColor,
    ShapeOverlayPayload,
    ShapePrimitive,
)
from paperworkshell.preflight.core_rules import (
    MAX_CORE_PAGE_DIMENSION_MILLIPOINTS,
    core_preflight_rules,
)
from paperworkshell.preflight.engine import PreflightEngine
from paperworkshell.preflight.facts import (
    CasePageDetailsAvailability,
    CasePagePreflightFact,
    CasePreflightFacts,
    CaseSourcePreflightFact,
    ChecklistFactAvailability,
    ChecklistPreflightFacts,
    OutputItemPreflightFact,
    OutputOverlayFlatteningAvailability,
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
    PreflightInput,
    PreflightReport,
    PreflightScope,
    PreflightScopeCatalog,
    PreflightScopeKind,
    PreflightSeverity,
)

CASE_ID = UUID("10000000-0000-4000-8000-000000000001")
SOURCE_ID = UUID("10000000-0000-4000-8000-000000000002")
PAGE_ID = UUID("10000000-0000-4000-8000-000000000003")
SECOND_PAGE_ID = UUID("10000000-0000-4000-8000-000000000004")
INACTIVE_PAGE_ID = UUID("10000000-0000-4000-8000-000000000005")
ASSIGNMENT_ID = UUID("10000000-0000-4000-8000-000000000006")
CONFIRMATION_ID = UUID("10000000-0000-4000-8000-000000000007")
SECOND_ASSIGNMENT_ID = UUID("10000000-0000-4000-8000-000000000008")
SECOND_CONFIRMATION_ID = UUID("10000000-0000-4000-8000-000000000009")
THIRD_ASSIGNMENT_ID = UUID("10000000-0000-4000-8000-00000000000a")
THIRD_CONFIRMATION_ID = UUID("10000000-0000-4000-8000-00000000000b")
REQUIREMENT_ID = "identity"
OUTPUT_KEY = "packet.primary"
MANIFEST_REVISION = 11
RECORDED_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
TEMPLATE_BINDING = TemplateBinding("synthetic.application", "1.0.0", "a" * 64)


def _definition() -> ChecklistDefinition:
    return ChecklistDefinition(
        TEMPLATE_BINDING,
        (
            ChecklistRequirement(
                REQUIREMENT_ID,
                RequirementCountUnit.DOCUMENTS,
                1,
                2,
            ),
        ),
    )


def _assignment(
    *,
    assignment_id: UUID = ASSIGNMENT_ID,
    confirmation_id: UUID = CONFIRMATION_ID,
    page_ids: tuple[UUID, ...] = (PAGE_ID, SECOND_PAGE_ID),
    review_state: AssignmentReviewState = AssignmentReviewState.USER_CONFIRMED,
    origin: AssignmentOrigin = AssignmentOrigin.USER,
) -> ChecklistAssignment:
    return ChecklistAssignment(
        assignment_id=assignment_id,
        requirement_id=REQUIREMENT_ID,
        page_ids=page_ids,
        origin=origin,
        review_state=review_state,
        confirmation_id=(
            confirmation_id if review_state is AssignmentReviewState.USER_CONFIRMED else None
        ),
        assigned_at=RECORDED_AT,
    )


def _ready_checklist(
    *,
    active_page_ids: tuple[UUID, ...] = (PAGE_ID, SECOND_PAGE_ID),
) -> ChecklistPreflightFacts:
    definition = _definition()
    assignment = _assignment(page_ids=active_page_ids)
    return ChecklistPreflightFacts(
        case_id=CASE_ID,
        manifest_revision=MANIFEST_REVISION,
        availability=ChecklistFactAvailability.CURRENT,
        definition=definition,
        projection=ChecklistProjection(
            requirements=(
                ChecklistRequirementProjection(
                    definition.requirements[0],
                    (assignment,),
                    1,
                    ChecklistStatus.READY,
                ),
            ),
            active_page_ids=active_page_ids,
            unassigned_page_ids=(),
            unconfirmed_page_ids=(),
        ),
    )


def _incomplete_checklist() -> ChecklistPreflightFacts:
    definition = _definition()
    return ChecklistPreflightFacts(
        case_id=CASE_ID,
        manifest_revision=MANIFEST_REVISION,
        availability=ChecklistFactAvailability.CURRENT,
        definition=definition,
        projection=ChecklistProjection(
            requirements=(
                ChecklistRequirementProjection(
                    definition.requirements[0],
                    (),
                    0,
                    ChecklistStatus.MISSING,
                ),
            ),
            active_page_ids=(PAGE_ID, SECOND_PAGE_ID),
            unassigned_page_ids=(PAGE_ID, SECOND_PAGE_ID),
            unconfirmed_page_ids=(),
        ),
    )


def _pending_checklist() -> ChecklistPreflightFacts:
    definition = _definition()
    assignment = _assignment(review_state=AssignmentReviewState.SUGGESTED)
    return ChecklistPreflightFacts(
        case_id=CASE_ID,
        manifest_revision=MANIFEST_REVISION,
        availability=ChecklistFactAvailability.CURRENT,
        definition=definition,
        projection=ChecklistProjection(
            requirements=(
                ChecklistRequirementProjection(
                    definition.requirements[0],
                    (assignment,),
                    0,
                    ChecklistStatus.ADDED,
                ),
            ),
            active_page_ids=(PAGE_ID, SECOND_PAGE_ID),
            unassigned_page_ids=(),
            unconfirmed_page_ids=(PAGE_ID, SECOND_PAGE_ID),
        ),
    )


def _empty_assignment_checklist() -> ChecklistPreflightFacts:
    definition = _definition()
    assignment = _assignment(page_ids=(INACTIVE_PAGE_ID,))
    return ChecklistPreflightFacts(
        case_id=CASE_ID,
        manifest_revision=MANIFEST_REVISION,
        availability=ChecklistFactAvailability.CURRENT,
        definition=definition,
        projection=ChecklistProjection(
            requirements=(
                ChecklistRequirementProjection(
                    definition.requirements[0],
                    (assignment,),
                    0,
                    ChecklistStatus.NEEDS_REVIEW,
                ),
            ),
            active_page_ids=(PAGE_ID,),
            unassigned_page_ids=(PAGE_ID,),
            unconfirmed_page_ids=(),
        ),
    )


def _over_limit_checklist() -> ChecklistPreflightFacts:
    definition = _definition()
    assignments = (
        _assignment(page_ids=(PAGE_ID,)),
        _assignment(
            assignment_id=SECOND_ASSIGNMENT_ID,
            confirmation_id=SECOND_CONFIRMATION_ID,
            page_ids=(SECOND_PAGE_ID,),
        ),
        _assignment(
            assignment_id=THIRD_ASSIGNMENT_ID,
            confirmation_id=THIRD_CONFIRMATION_ID,
            page_ids=(INACTIVE_PAGE_ID,),
        ),
    )
    return ChecklistPreflightFacts(
        case_id=CASE_ID,
        manifest_revision=MANIFEST_REVISION,
        availability=ChecklistFactAvailability.CURRENT,
        definition=definition,
        projection=ChecklistProjection(
            requirements=(
                ChecklistRequirementProjection(
                    definition.requirements[0],
                    assignments,
                    3,
                    ChecklistStatus.NEEDS_REVIEW,
                ),
            ),
            active_page_ids=(PAGE_ID, SECOND_PAGE_ID, INACTIVE_PAGE_ID),
            unassigned_page_ids=(),
            unconfirmed_page_ids=(),
        ),
    )


def _feature_summary(
    *,
    signature_markers: PdfFeatureState = PdfFeatureState.NOT_DETECTED,
    attachments: PdfFeatureState = PdfFeatureState.NOT_DETECTED,
    interactive_content: PdfFeatureState = PdfFeatureState.NOT_DETECTED,
    actions: PdfFeatureState = PdfFeatureState.NOT_DETECTED,
    scripts: PdfFeatureState = PdfFeatureState.NOT_DETECTED,
    xfa: PdfFeatureState = PdfFeatureState.NOT_DETECTED,
    metadata: PdfFeatureState = PdfFeatureState.UNKNOWN,
    annotations: PdfFeatureState = PdfFeatureState.UNKNOWN,
    form_fields: PdfFeatureState = PdfFeatureState.UNKNOWN,
) -> PdfFeatureSummary:
    return PdfFeatureSummary(
        signature_markers,
        attachments,
        interactive_content,
        actions,
        scripts,
        xfa,
        metadata=metadata,
        annotations=annotations,
        form_fields=form_fields,
    )


def _opened_pdf(
    *,
    features: PdfFeatureSummary | None = None,
    structural_state: PdfStructuralState = PdfStructuralState.CLEAN,
) -> PdfInspectionResult:
    return PdfInspectionResult(
        PdfOpenState.OPENED,
        2,
        20,
        PdfEncryptionState.UNENCRYPTED,
        structural_state,
        _feature_summary() if features is None else features,
    )


def _pdf_inspection(
    result: PdfInspectionResult | None,
    *,
    availability: SourceInspectionAvailability = SourceInspectionAvailability.CURRENT,
    saved_signature_state: SignedSourceSignatureState | None = SignedSourceSignatureState.UNKNOWN,
) -> SourceInspectionFact:
    return SourceInspectionFact(
        source_id=SOURCE_ID,
        position=1,
        media_type=DetectedMediaType.PDF,
        availability=availability,
        source_sha256="b" * 64,
        pdf_result=result,
        saved_signature_state=saved_signature_state,
    )


def _image_inspection() -> SourceInspectionFact:
    return SourceInspectionFact(
        source_id=SOURCE_ID,
        position=1,
        media_type=DetectedMediaType.PNG,
        availability=SourceInspectionAvailability.NOT_APPLICABLE,
        source_sha256="b" * 64,
    )


def _page_details(
    active_page_ids: tuple[UUID, ...] = (PAGE_ID, SECOND_PAGE_ID),
    *,
    first_width_millipoints: int = 595_000,
    first_overlay_count: int = 0,
    first_unapplied_overlay_count: int = 0,
    first_ordinary_shape_count: int = 0,
) -> tuple[CasePagePreflightFact, ...]:
    return tuple(
        CasePagePreflightFact(
            page_id=page_id,
            position=position,
            source_id=SOURCE_ID,
            source_page_number=position,
            width_millipoints=(first_width_millipoints if position == 1 else 595_000),
            height_millipoints=842_000,
            overlay_count=first_overlay_count if position == 1 else 0,
            unapplied_overlay_count=(first_unapplied_overlay_count if position == 1 else 0),
            ordinary_shape_count=first_ordinary_shape_count if position == 1 else 0,
        )
        for position, page_id in enumerate(active_page_ids, start=1)
    )


def _default_template() -> TemplatePreflightFacts:
    return TemplatePreflightFacts(
        CASE_ID,
        MANIFEST_REVISION,
        TemplateFactAvailability.CURRENT,
        TEMPLATE_BINDING,
        (),
    )


def _default_quality(active_page_ids: tuple[UUID, ...]) -> QualityPreflightFacts:
    return QualityPreflightFacts(
        CASE_ID,
        MANIFEST_REVISION,
        QualityFactAvailability.CURRENT,
        active_page_ids,
        (),
        "balanced.v1",
        "c" * 64,
    )


def _default_output(
    active_page_ids: tuple[UUID, ...],
    *,
    overlay_flattening_availability: OutputOverlayFlatteningAvailability = (
        OutputOverlayFlatteningAvailability.CURRENT
    ),
    flattened_overlay_page_ids: tuple[UUID, ...] = (),
) -> OutputPlanPreflightFacts:
    return OutputPlanPreflightFacts(
        CASE_ID,
        MANIFEST_REVISION,
        OutputPlanAvailability.CURRENT,
        "single.packet",
        (OutputItemPreflightFact(OUTPUT_KEY, 1, active_page_ids, 10_000),),
        overlay_flattening_availability,
        flattened_overlay_page_ids,
    )


def _evaluate(
    *,
    checklist: ChecklistPreflightFacts | None = None,
    template: TemplatePreflightFacts | None = None,
    inspections: tuple[SourceInspectionFact, ...] = (),
    quality: QualityPreflightFacts | None = None,
    output: OutputPlanPreflightFacts | None = None,
    active_page_ids: tuple[UUID, ...] = (PAGE_ID, SECOND_PAGE_ID),
    page_details_availability: CasePageDetailsAvailability = (
        CasePageDetailsAvailability.UNAVAILABLE
    ),
    page_details: tuple[CasePagePreflightFact, ...] = (),
) -> PreflightReport:
    selected_checklist = (
        _ready_checklist(active_page_ids=active_page_ids) if checklist is None else checklist
    )
    selected_template = _default_template() if template is None else template
    selected_quality = _default_quality(active_page_ids) if quality is None else quality
    selected_output = (
        _default_output(
            active_page_ids,
            flattened_overlay_page_ids=tuple(
                page.page_id for page in page_details if page.overlay_count > 0
            ),
        )
        if output is None
        else output
    )
    source_facts = SourceInspectionsPreflightFacts(
        CASE_ID,
        MANIFEST_REVISION,
        inspections,
    )
    case = CasePreflightFacts(
        CASE_ID,
        MANIFEST_REVISION,
        selected_template.binding,
        tuple(
            CaseSourcePreflightFact(
                item.source_id,
                item.position,
                item.media_type,
                item.source_sha256,
                item.saved_signature_state,
            )
            for item in inspections
        ),
        active_page_ids,
        page_details_availability,
        page_details,
    )
    scopes: list[PreflightScope] = [
        PreflightScope(PreflightScopeKind.CASE, CASE_ID),
    ]
    if (
        selected_checklist.availability is ChecklistFactAvailability.CURRENT
        and selected_checklist.definition is not None
        and selected_checklist.projection is not None
    ):
        scopes.extend(
            PreflightScope(
                PreflightScopeKind.REQUIREMENT,
                requirement.requirement_id,
                position,
            )
            for position, requirement in enumerate(
                selected_checklist.definition.requirements,
                start=1,
            )
        )
    scopes.extend(
        PreflightScope(PreflightScopeKind.SOURCE, item.source_id, item.position)
        for item in inspections
    )
    if (
        selected_checklist.availability is ChecklistFactAvailability.CURRENT
        and selected_checklist.projection is not None
    ):
        assignments = tuple(
            assignment
            for requirement in selected_checklist.projection.requirements
            for assignment in requirement.assignments
        )
        scopes.extend(
            PreflightScope(
                PreflightScopeKind.ASSIGNMENT,
                assignment.assignment_id,
                position,
            )
            for position, assignment in enumerate(assignments, start=1)
        )
    scopes.extend(
        PreflightScope(PreflightScopeKind.PAGE, page_id, position)
        for position, page_id in enumerate(active_page_ids, start=1)
    )
    if selected_output.availability is OutputPlanAvailability.CURRENT:
        scopes.extend(
            PreflightScope(
                PreflightScopeKind.OUTPUT,
                item.output_key,
                item.position,
            )
            for item in selected_output.items
        )
    authority = PreflightAuthority(
        CASE_ID,
        MANIFEST_REVISION,
        "d" * 64,
        selected_template.binding,
        PreflightScopeCatalog(CASE_ID, tuple(scopes)),
    )
    values = (
        case,
        selected_template,
        selected_checklist,
        source_facts,
        selected_quality,
        selected_output,
    )
    inputs = tuple(
        PreflightInput(authority, dependency, value)
        for dependency, value in zip(PreflightDependency, values, strict=True)
    )
    return PreflightEngine(core_preflight_rules()).evaluate(inputs).report


def _codes(report: PreflightReport) -> set[str]:
    return {finding.code for finding in report.findings}


def test_complete_and_incomplete_packets_have_concrete_completeness_results() -> None:
    image = _image_inspection()
    complete = _evaluate(
        inspections=(image,),
        page_details_availability=CasePageDetailsAvailability.CURRENT,
        page_details=_page_details(),
    )
    incomplete = _evaluate(
        checklist=_incomplete_checklist(),
        inspections=(image,),
        page_details_availability=CasePageDetailsAvailability.CURRENT,
        page_details=_page_details(),
    )

    assert complete.blocking_errors == ()
    assert complete.confirmations_required == ()
    assert complete.findings == ()
    assert {
        "CHECKLIST_MINIMUM_NOT_MET",
        "CHECKLIST_PAGE_UNASSIGNED",
    } <= _codes(incomplete)
    assert (
        len(
            [
                finding
                for finding in incomplete.findings
                if finding.code == "CHECKLIST_PAGE_UNASSIGNED"
            ]
        )
        == 2
    )


def test_pending_and_empty_assignments_do_not_satisfy_completeness() -> None:
    pending = _evaluate(checklist=_pending_checklist())
    empty = _evaluate(
        checklist=_empty_assignment_checklist(),
        active_page_ids=(PAGE_ID,),
    )

    assert "CHECKLIST_ASSIGNMENT_CONFIRMATION_REQUIRED" in _codes(pending)
    assert any(
        finding.code == "CHECKLIST_ASSIGNMENT_CONFIRMATION_REQUIRED"
        and finding.severity is PreflightSeverity.CONFIRMATION_REQUIRED
        for finding in pending.findings
    )
    assert "CHECKLIST_ASSIGNMENT_EMPTY" in _codes(empty)
    assert "CHECKLIST_REQUIREMENT_NEEDS_REVIEW" in _codes(empty)


def test_over_limit_confirmed_count_is_blocking_and_bounded_to_requirement() -> None:
    report = _evaluate(
        checklist=_over_limit_checklist(),
        active_page_ids=(PAGE_ID, SECOND_PAGE_ID, INACTIVE_PAGE_ID),
    )
    finding = next(item for item in report.findings if item.code == "CHECKLIST_MAXIMUM_EXCEEDED")

    assert finding.severity is PreflightSeverity.BLOCKING_ERROR
    assert finding.scope == PreflightScope(
        PreflightScopeKind.REQUIREMENT,
        REQUIREMENT_ID,
        1,
    )
    assert finding.evidence[0].observed_value == 3
    assert finding.evidence[0].limit_value == 2


def test_unavailable_states_are_not_treated_as_clean_or_absent() -> None:
    unavailable_inspection = _pdf_inspection(
        None,
        availability=SourceInspectionAvailability.UNAVAILABLE,
    )
    report = _evaluate(inspections=(unavailable_inspection,))
    unverified = next(
        finding for finding in report.findings if finding.code == "SOURCE_INSPECTION_UNAVAILABLE"
    )

    assert unverified.severity is PreflightSeverity.WARNING
    assert "unverified" in unverified.message.casefold()
    assert "do not treat unavailable" in unverified.remediation.casefold()
    assert not {
        "PDF_ACTIONS_UNSUPPORTED",
        "PDF_ATTACHMENTS_DETECTED",
        "PDF_SCRIPTS_UNSUPPORTED",
        "PDF_XFA_UNSUPPORTED",
    } & _codes(report)


def test_missing_template_checklist_and_output_plan_block_completion() -> None:
    template = TemplatePreflightFacts(
        CASE_ID,
        MANIFEST_REVISION,
        TemplateFactAvailability.NOT_SELECTED,
        None,
        (),
    )
    checklist = ChecklistPreflightFacts(
        CASE_ID,
        MANIFEST_REVISION,
        ChecklistFactAvailability.UNAVAILABLE,
        None,
        None,
    )
    output = OutputPlanPreflightFacts(
        CASE_ID,
        MANIFEST_REVISION,
        OutputPlanAvailability.UNAVAILABLE,
        "single.packet",
        (),
    )
    report = _evaluate(
        template=template,
        checklist=checklist,
        output=output,
    )

    assert {
        "TEMPLATE_NOT_SELECTED",
        "CHECKLIST_UNAVAILABLE",
        "OUTPUT_PLAN_UNAVAILABLE",
    } <= {finding.code for finding in report.blocking_errors}


def test_template_review_and_confirmation_notices_remain_user_actions() -> None:
    template = TemplatePreflightFacts(
        CASE_ID,
        MANIFEST_REVISION,
        TemplateFactAvailability.CURRENT,
        TEMPLATE_BINDING,
        (
            TemplateNoticeFact(
                "review.identity",
                TemplateNoticeKind.NEEDS_REVIEW,
                1,
                "Review the identity assignment.",
                REQUIREMENT_ID,
            ),
            TemplateNoticeFact(
                "review.identity.second",
                TemplateNoticeKind.NEEDS_REVIEW,
                2,
                "Review the identity assignment again.",
                REQUIREMENT_ID,
            ),
            TemplateNoticeFact(
                "confirm.packet",
                TemplateNoticeKind.CONFIRMATION,
                3,
                "Confirm the packet review.",
            ),
        ),
    )
    report = _evaluate(template=template)

    reviews = tuple(
        finding
        for finding in report.findings
        if finding.code.startswith("TEMPLATE_STATEMENT_NEEDS_REVIEW_")
    )
    confirmation = next(
        finding
        for finding in report.findings
        if finding.code == "TEMPLATE_STATEMENT_CONFIRMATION_REQUIRED_0003"
    )
    assert {finding.code for finding in reviews} == {
        "TEMPLATE_STATEMENT_NEEDS_REVIEW_0001",
        "TEMPLATE_STATEMENT_NEEDS_REVIEW_0002",
    }
    assert all(finding.severity is PreflightSeverity.WARNING for finding in reviews)
    assert all(finding.scope.kind is PreflightScopeKind.REQUIREMENT for finding in reviews)
    assert confirmation.severity is PreflightSeverity.CONFIRMATION_REQUIRED


def test_signed_source_is_reported_as_an_unsigned_derivative_boundary() -> None:
    result = _opened_pdf(
        features=_feature_summary(signature_markers=PdfFeatureState.DETECTED),
    )
    report = _evaluate(inspections=(_pdf_inspection(result),))
    finding = next(
        item for item in report.findings if item.code == "PDF_SIGNATURE_MARKERS_UNSIGNED_DERIVATIVE"
    )

    assert finding.severity is PreflightSeverity.WARNING
    assert "unsigned derivative" in finding.remediation.casefold()
    assert "signature markers" in finding.message.casefold()


def test_xfa_scripts_actions_interactivity_and_attachments_are_reported() -> None:
    result = _opened_pdf(
        features=_feature_summary(
            attachments=PdfFeatureState.DETECTED,
            interactive_content=PdfFeatureState.DETECTED,
            actions=PdfFeatureState.DETECTED,
            scripts=PdfFeatureState.DETECTED,
            xfa=PdfFeatureState.DETECTED,
        )
    )
    report = _evaluate(inspections=(_pdf_inspection(result),))
    by_code = {finding.code: finding for finding in report.findings}

    assert {
        "PDF_XFA_UNSUPPORTED",
        "PDF_SCRIPTS_UNSUPPORTED",
        "PDF_ACTIONS_UNSUPPORTED",
        "PDF_INTERACTIVE_CONTENT_UNSUPPORTED",
        "PDF_ATTACHMENTS_DETECTED",
    } <= set(by_code)
    assert by_code["PDF_ATTACHMENTS_DETECTED"].severity is PreflightSeverity.WARNING
    assert all(
        by_code[code].severity is PreflightSeverity.BLOCKING_ERROR
        for code in (
            "PDF_XFA_UNSUPPORTED",
            "PDF_SCRIPTS_UNSUPPORTED",
            "PDF_ACTIONS_UNSUPPORTED",
            "PDF_INTERACTIVE_CONTENT_UNSUPPORTED",
        )
    )


@pytest.mark.parametrize(
    ("result", "expected_code"),
    (
        (
            PdfInspectionResult(
                PdfOpenState.ENCRYPTED,
                None,
                None,
                PdfEncryptionState.ENCRYPTED,
                PdfStructuralState.FAILED,
                PdfFeatureSummary.unknown(),
            ),
            "PDF_ENCRYPTED",
        ),
        (
            PdfInspectionResult(
                PdfOpenState.MALFORMED,
                None,
                None,
                PdfEncryptionState.UNKNOWN,
                PdfStructuralState.FAILED,
                PdfFeatureSummary.unknown(),
            ),
            "PDF_STRUCTURE_FAILED",
        ),
        (
            _opened_pdf(structural_state=PdfStructuralState.WARNINGS),
            "PDF_STRUCTURE_WARNINGS",
        ),
    ),
)
def test_encryption_and_structure_failures_are_concrete(
    result: PdfInspectionResult,
    expected_code: str,
) -> None:
    report = _evaluate(inspections=(_pdf_inspection(result),))

    assert expected_code in _codes(report)


def test_page_details_and_metadata_remain_unverified_when_authority_is_unavailable() -> None:
    report = _evaluate(inspections=(_pdf_inspection(_opened_pdf()),))
    by_code = {finding.code: finding for finding in report.findings}

    assert "CASE_PAGE_DETAILS_UNVERIFIED" in by_code
    assert "page dimensions" in by_code["CASE_PAGE_DETAILS_UNVERIFIED"].message.casefold()
    assert "overlay application" in by_code["CASE_PAGE_DETAILS_UNVERIFIED"].message.casefold()
    assert "PDF_METADATA_UNVERIFIED" in by_code
    assert all(by_code[code].severity is PreflightSeverity.WARNING for code in by_code)


@pytest.mark.parametrize(
    ("feature", "detected_code", "unknown_code", "severity"),
    (
        (
            "metadata",
            "PDF_METADATA_DETECTED",
            "PDF_METADATA_UNVERIFIED",
            PreflightSeverity.WARNING,
        ),
        (
            "annotations",
            "PDF_ANNOTATIONS_DETECTED",
            "PDF_ANNOTATIONS_UNVERIFIED",
            PreflightSeverity.WARNING,
        ),
        (
            "form_fields",
            "PDF_FORM_FIELDS_UNSUPPORTED",
            "PDF_FORM_FIELDS_UNVERIFIED",
            PreflightSeverity.BLOCKING_ERROR,
        ),
    ),
)
@pytest.mark.parametrize("state", tuple(PdfFeatureState))
def test_expanded_features_have_distinct_detected_unknown_and_not_detected_findings(
    feature: str,
    detected_code: str,
    unknown_code: str,
    severity: PreflightSeverity,
    state: PdfFeatureState,
) -> None:
    features = replace(
        _feature_summary(
            metadata=PdfFeatureState.NOT_DETECTED,
            annotations=PdfFeatureState.NOT_DETECTED,
            form_fields=PdfFeatureState.NOT_DETECTED,
        ),
        **{feature: state},
    )
    report = _evaluate(inspections=(_pdf_inspection(_opened_pdf(features=features)),))
    findings = {finding.code: finding for finding in report.findings}

    if state is PdfFeatureState.DETECTED:
        assert detected_code in findings
        assert findings[detected_code].severity is severity
        assert findings[detected_code].scope == PreflightScope(
            PreflightScopeKind.SOURCE, SOURCE_ID, 1
        )
        assert unknown_code not in findings
    elif state is PdfFeatureState.UNKNOWN:
        assert unknown_code in findings
        assert findings[unknown_code].severity is PreflightSeverity.WARNING
        assert "unknown state" in findings[unknown_code].remediation
        assert detected_code not in findings
    else:
        assert detected_code not in findings
        assert unknown_code not in findings
    assert ("PDF_FEATURES_UNVERIFIED" in findings) is (state is PdfFeatureState.UNKNOWN)


def test_partial_unknown_inventory_keeps_positive_findings_and_source_scope() -> None:
    first = _pdf_inspection(
        _opened_pdf(
            features=_feature_summary(
                metadata=PdfFeatureState.DETECTED,
                annotations=PdfFeatureState.DETECTED,
                form_fields=PdfFeatureState.UNKNOWN,
                scripts=PdfFeatureState.DETECTED,
            )
        )
    )
    second_id = UUID("10000000-0000-4000-8000-00000000000c")
    second = replace(
        _pdf_inspection(_opened_pdf()), source_id=second_id, position=2, source_sha256="c" * 64
    )
    report = _evaluate(inspections=(first, second))
    first_codes = {item.code for item in report.findings if item.scope.item_id == SOURCE_ID}
    second_codes = {item.code for item in report.findings if item.scope.item_id == second_id}

    assert {
        "PDF_FEATURES_UNVERIFIED",
        "PDF_FORM_FIELDS_UNVERIFIED",
        "PDF_METADATA_DETECTED",
        "PDF_ANNOTATIONS_DETECTED",
        "PDF_SCRIPTS_UNSUPPORTED",
    } <= first_codes
    assert "PDF_METADATA_UNVERIFIED" not in first_codes
    assert "PDF_ANNOTATIONS_UNVERIFIED" not in first_codes
    assert "PDF_METADATA_UNVERIFIED" in second_codes
    assert "PDF_METADATA_DETECTED" not in second_codes
    assert "PDF_SCRIPTS_UNSUPPORTED" not in second_codes


def test_legacy_inspection_never_implies_new_feature_absence() -> None:
    legacy = replace(_opened_pdf(), report_version=1)
    report = _evaluate(inspections=(_pdf_inspection(legacy),))

    assert {
        "PDF_FEATURES_UNVERIFIED",
        "PDF_METADATA_UNVERIFIED",
        "PDF_ANNOTATIONS_UNVERIFIED",
        "PDF_FORM_FIELDS_UNVERIFIED",
    } <= _codes(report)
    compatibility = next(
        rule for rule in core_preflight_rules() if rule.descriptor.rule_id == "core.compatibility"
    )
    assert compatibility.descriptor.rule_version == 2


@pytest.mark.parametrize("has_signature_field", (False, True))
def test_annotation_review_and_signature_warning_do_not_replace_interactive_blocker(
    has_signature_field: bool,
) -> None:
    features = _feature_summary(
        annotations=PdfFeatureState.DETECTED,
        interactive_content=PdfFeatureState.DETECTED,
        form_fields=(
            PdfFeatureState.DETECTED if has_signature_field else PdfFeatureState.NOT_DETECTED
        ),
        signature_markers=(
            PdfFeatureState.DETECTED if has_signature_field else PdfFeatureState.NOT_DETECTED
        ),
    )
    report = _evaluate(inspections=(_pdf_inspection(_opened_pdf(features=features)),))
    findings = {finding.code: finding for finding in report.findings}

    assert findings["PDF_ANNOTATIONS_DETECTED"].severity is PreflightSeverity.WARNING
    assert (
        findings["PDF_INTERACTIVE_CONTENT_UNSUPPORTED"].severity is PreflightSeverity.BLOCKING_ERROR
    )
    if has_signature_field:
        assert findings["PDF_FORM_FIELDS_UNSUPPORTED"].severity is PreflightSeverity.BLOCKING_ERROR
        signature = findings["PDF_SIGNATURE_MARKERS_UNSIGNED_DERIVATIVE"]
        assert signature.severity is PreflightSeverity.WARNING
        assert "unsigned derivative" in signature.remediation.casefold()
    assert all(finding.confirmation is None for finding in findings.values())


@pytest.mark.parametrize(
    "saved_state",
    (
        None,
        SignedSourceSignatureState.NONE_DETECTED,
        SignedSourceSignatureState.MARKERS_DETECTED,
        SignedSourceSignatureState.UNKNOWN,
    ),
)
@pytest.mark.parametrize("fresh_state", tuple(PdfFeatureState))
def test_fresh_signature_risk_requires_saved_consent_scope(
    saved_state: SignedSourceSignatureState | None,
    fresh_state: PdfFeatureState,
) -> None:
    report = _evaluate(
        inspections=(
            _pdf_inspection(
                _opened_pdf(features=_feature_summary(signature_markers=fresh_state)),
                saved_signature_state=saved_state,
            ),
        )
    )
    findings = {finding.code: finding for finding in report.findings}
    must_block = fresh_state in {
        PdfFeatureState.DETECTED,
        PdfFeatureState.UNKNOWN,
    } and saved_state in {None, SignedSourceSignatureState.NONE_DETECTED}

    assert ("PDF_SIGNATURE_STATE_REVIEW_REQUIRED" in findings) is must_block
    if must_block:
        finding = findings["PDF_SIGNATURE_STATE_REVIEW_REQUIRED"]
        assert finding.severity is PreflightSeverity.BLOCKING_ERROR
        assert finding.confirmation is None
        assert finding.scope == PreflightScope(PreflightScopeKind.SOURCE, SOURCE_ID, 1)
        assert "acknowledging warnings does not resolve" in finding.remediation
    assert ("PDF_SIGNATURE_MARKERS_UNSIGNED_DERIVATIVE" in findings) is (
        fresh_state is PdfFeatureState.DETECTED
    )


def test_extreme_page_dimension_uses_strict_14_400_point_boundary() -> None:
    image = _image_inspection()
    extreme = _evaluate(
        inspections=(image,),
        page_details_availability=CasePageDetailsAvailability.CURRENT,
        page_details=_page_details(first_width_millipoints=MAX_CORE_PAGE_DIMENSION_MILLIPOINTS + 1),
    )
    boundary = _evaluate(
        inspections=(image,),
        page_details_availability=CasePageDetailsAvailability.CURRENT,
        page_details=_page_details(first_width_millipoints=MAX_CORE_PAGE_DIMENSION_MILLIPOINTS),
    )
    finding = next(item for item in extreme.findings if item.code == "PAGE_DIMENSIONS_EXTREME")

    assert finding.severity is PreflightSeverity.WARNING
    assert finding.scope == PreflightScope(PreflightScopeKind.PAGE, PAGE_ID, 1)
    assert "PAGE_DIMENSIONS_EXTREME" not in _codes(boundary)


def test_unapplied_overlay_is_blocking() -> None:
    image = _image_inspection()
    report = _evaluate(
        inspections=(image,),
        page_details_availability=CasePageDetailsAvailability.CURRENT,
        page_details=_page_details(
            first_overlay_count=1,
            first_unapplied_overlay_count=1,
        ),
    )
    finding = next(item for item in report.findings if item.code == "PAGE_UNAPPLIED_OVERLAYS")

    assert finding.severity is PreflightSeverity.BLOCKING_ERROR
    assert finding.scope == PreflightScope(PreflightScopeKind.PAGE, PAGE_ID, 1)
    assert finding.evidence[0].observed_value == 1


@pytest.mark.parametrize(
    ("availability", "flattened_page_ids", "expected_code", "expected_severity"),
    (
        (
            OutputOverlayFlatteningAvailability.CURRENT,
            (),
            "PAGE_OVERLAYS_NOT_PLANNED_FOR_FLATTENING",
            PreflightSeverity.BLOCKING_ERROR,
        ),
        (
            OutputOverlayFlatteningAvailability.UNAVAILABLE,
            (),
            "PAGE_OVERLAY_FLATTENING_UNVERIFIED",
            PreflightSeverity.WARNING,
        ),
    ),
)
def test_overlay_flattening_plan_never_becomes_implicitly_clean(
    availability: OutputOverlayFlatteningAvailability,
    flattened_page_ids: tuple[UUID, ...],
    expected_code: str,
    expected_severity: PreflightSeverity,
) -> None:
    report = _evaluate(
        inspections=(_image_inspection(),),
        page_details=_page_details(
            first_overlay_count=1,
        ),
        page_details_availability=CasePageDetailsAvailability.CURRENT,
        output=_default_output(
            (PAGE_ID, SECOND_PAGE_ID),
            overlay_flattening_availability=availability,
            flattened_overlay_page_ids=flattened_page_ids,
        ),
    )

    finding = next(item for item in report.findings if item.code == expected_code)
    assert finding.severity is expected_severity


def test_planned_overlay_flattening_does_not_claim_final_export_verification() -> None:
    report = _evaluate(
        inspections=(_image_inspection(),),
        page_details=_page_details(
            first_overlay_count=1,
        ),
        page_details_availability=CasePageDetailsAvailability.CURRENT,
        output=_default_output(
            (PAGE_ID, SECOND_PAGE_ID),
            flattened_overlay_page_ids=(PAGE_ID,),
        ),
    )

    assert "PAGE_OVERLAYS_NOT_PLANNED_FOR_FLATTENING" not in _codes(report)
    assert "PAGE_OVERLAY_FLATTENING_UNVERIFIED" not in _codes(report)
    rendered = " ".join(
        f"{finding.message} {finding.remediation}" for finding in report.findings
    ).casefold()
    assert "verified flattened" not in rendered


def test_blur_ocr_and_duplicate_signals_remain_review_warnings() -> None:
    findings = tuple(
        sorted(
            (
                QualityFinding(
                    PAGE_ID,
                    1,
                    QualityFindingCode.LIKELY_BLUR,
                    QualityEvidenceKind.LAPLACIAN_MEAN_SQUARE,
                    4,
                    5,
                ),
                QualityFinding(
                    PAGE_ID,
                    1,
                    QualityFindingCode.OCR_FAILED,
                    QualityEvidenceKind.OCR_STATUS,
                    1,
                    0,
                ),
                QualityFinding(
                    SECOND_PAGE_ID,
                    2,
                    QualityFindingCode.SUSPECTED_DUPLICATE,
                    QualityEvidenceKind.DHASH_HAMMING_DISTANCE,
                    2,
                    3,
                    PAGE_ID,
                    1,
                ),
            ),
            key=quality_finding_sort_key,
        )
    )
    quality = QualityPreflightFacts(
        CASE_ID,
        MANIFEST_REVISION,
        QualityFactAvailability.CURRENT,
        (PAGE_ID, SECOND_PAGE_ID),
        findings,
        "balanced.v1",
        "c" * 64,
    )
    report = _evaluate(quality=quality)
    selected = tuple(finding for finding in report.findings if finding.code.startswith("QUALITY_"))

    assert {finding.code for finding in selected} == {
        "QUALITY_LIKELY_BLUR",
        "QUALITY_OCR_FAILED",
        "QUALITY_SUSPECTED_DUPLICATE",
    }
    assert all(finding.severity is PreflightSeverity.WARNING for finding in selected)
    duplicate = next(
        finding for finding in selected if finding.code == "QUALITY_SUSPECTED_DUPLICATE"
    )
    assert duplicate.related_scope == PreflightScope(
        PreflightScopeKind.PAGE,
        PAGE_ID,
        1,
    )


def test_every_registered_quality_projection_is_advisory() -> None:
    descriptor = next(
        rule.descriptor
        for rule in core_preflight_rules()
        if rule.descriptor.rule_id == "core.quality"
    )

    assert {definition.code for definition in descriptor.finding_definitions} == {
        "QUALITY_ANALYSIS_UNAVAILABLE",
        "QUALITY_LIKELY_BLANK",
        "QUALITY_LIKELY_BLUR",
        "QUALITY_LIKELY_LOW_RESOLUTION",
        "QUALITY_OCR_FAILED",
        "QUALITY_OCR_LOW_CONFIDENCE",
        "QUALITY_SUSPECTED_CLIPPING",
        "QUALITY_SUSPECTED_DUPLICATE",
        "QUALITY_SUSPECTED_SIDEWAYS_ORIENTATION",
    }
    assert all(
        definition.severity is PreflightSeverity.WARNING
        for definition in descriptor.finding_definitions
    )


def test_black_shape_never_satisfies_permanent_removal_or_overlay_flattening() -> None:
    black = RgbaColor("#000000ff")
    shape = ShapeOverlayPayload(
        OverlayKind.SHAPE,
        ShapePrimitive.RECTANGLE,
        black,
        black,
    )
    image = _image_inspection()
    report = _evaluate(
        inspections=(image,),
        page_details_availability=CasePageDetailsAvailability.CURRENT,
        page_details=_page_details(
            first_overlay_count=1,
            first_ordinary_shape_count=1,
        ),
    )
    privacy = next(
        finding
        for finding in report.findings
        if finding.code == "PAGE_ORDINARY_SHAPES_NOT_PERMANENT_REMOVAL"
    )

    assert shape.intent == ORDINARY_SHAPE_INTENT
    assert privacy.severity is PreflightSeverity.WARNING
    assert "ordinary visual shapes" in privacy.message.casefold()
    assert "visual marks only" in privacy.remediation.casefold()
    assert "permanent removal" in privacy.message.casefold()
    assert "PAGE_UNAPPLIED_OVERLAYS" not in _codes(report)
    assert all("REDACTION_VERIFIED" not in finding.code for finding in report.findings)

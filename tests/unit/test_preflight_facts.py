"""PWS-0404 contracts for canonical, path-free preflight fact projections."""

from __future__ import annotations

from dataclasses import fields, replace
from uuid import UUID

import pytest

from paperworkshell.domain.checklist import (
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
)
from paperworkshell.domain.signed_source_policy import SignedSourceSignatureState
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
    validate_canonical_preflight_bindings,
)
from paperworkshell.preflight.models import (
    PreflightAuthority,
    PreflightDependency,
    PreflightError,
    PreflightErrorCode,
    PreflightFactValue,
    PreflightInput,
    PreflightScope,
    PreflightScopeCatalog,
    PreflightScopeKind,
)

CASE_ID = UUID("00000000-0000-4000-8000-000000000001")
OTHER_CASE_ID = UUID("00000000-0000-4000-8000-000000000002")
PDF_SOURCE_ID = UUID("00000000-0000-4000-8000-000000000011")
UNAVAILABLE_PDF_SOURCE_ID = UUID("00000000-0000-4000-8000-000000000012")
IMAGE_SOURCE_ID = UUID("00000000-0000-4000-8000-000000000013")
PAGE_ID = UUID("00000000-0000-4000-8000-000000000021")
SECOND_PAGE_ID = UUID("00000000-0000-4000-8000-000000000022")
OUTSIDE_PAGE_ID = UUID("00000000-0000-4000-8000-000000000023")
MANIFEST_REVISION = 7
MANIFEST_SHA256 = "1" * 64
PROFILE_SHA256 = "2" * 64
SOURCE_SHA256 = "3" * 64
SECOND_SOURCE_SHA256 = "4" * 64
IMAGE_SHA256 = "5" * 64
OUTPUT_KEY = "packet.primary"
REQUIREMENT_ID = "identity"
TEMPLATE_BINDING = TemplateBinding(
    template_id="synthetic.application",
    template_version="1.0.0",
    sha256="a" * 64,
)


def _template_binding() -> TemplateBinding:
    return TEMPLATE_BINDING


def _definition() -> ChecklistDefinition:
    return ChecklistDefinition(
        binding=_template_binding(),
        requirements=(
            ChecklistRequirement(
                requirement_id=REQUIREMENT_ID,
                count_unit=RequirementCountUnit.DOCUMENTS,
                minimum_count=1,
                maximum_count=2,
            ),
        ),
    )


def _projection(
    definition: ChecklistDefinition | None = None,
    *,
    page_ids: tuple[UUID, ...] = (PAGE_ID, SECOND_PAGE_ID),
) -> ChecklistProjection:
    selected = _definition() if definition is None else definition
    return ChecklistProjection(
        requirements=(
            ChecklistRequirementProjection(
                requirement=selected.requirements[0],
                assignments=(),
                confirmed_count=0,
                status=ChecklistStatus.MISSING,
            ),
        ),
        active_page_ids=page_ids,
        unassigned_page_ids=page_ids,
        unconfirmed_page_ids=(),
    )


def _pdf_result() -> PdfInspectionResult:
    return PdfInspectionResult(
        open_state=PdfOpenState.OPENED,
        page_count=2,
        object_count=37,
        encryption_state=PdfEncryptionState.UNENCRYPTED,
        structural_state=PdfStructuralState.WARNINGS,
        features=PdfFeatureSummary(
            signature_markers=PdfFeatureState.DETECTED,
            attachments=PdfFeatureState.NOT_DETECTED,
            interactive_content=PdfFeatureState.DETECTED,
            actions=PdfFeatureState.NOT_DETECTED,
            scripts=PdfFeatureState.DETECTED,
            xfa=PdfFeatureState.NOT_DETECTED,
        ),
    )


def _quality_finding(*, page_position: int = 1) -> QualityFinding:
    return QualityFinding(
        page_id=PAGE_ID,
        page_position=page_position,
        code=QualityFindingCode.LIKELY_LOW_RESOLUTION,
        evidence_kind=QualityEvidenceKind.SOURCE_SHORT_EDGE_PIXELS,
        observed_value=799,
        threshold_value=800,
    )


def _canonical_facts() -> tuple[
    CasePreflightFacts,
    TemplatePreflightFacts,
    ChecklistPreflightFacts,
    SourceInspectionsPreflightFacts,
    QualityPreflightFacts,
    OutputPlanPreflightFacts,
]:
    definition = _definition()
    return (
        CasePreflightFacts(
            case_id=CASE_ID,
            manifest_revision=MANIFEST_REVISION,
            template_binding=_template_binding(),
            sources=(
                CaseSourcePreflightFact(
                    source_id=PDF_SOURCE_ID,
                    position=1,
                    media_type=DetectedMediaType.PDF,
                    source_sha256=SOURCE_SHA256,
                ),
                CaseSourcePreflightFact(
                    source_id=UNAVAILABLE_PDF_SOURCE_ID,
                    position=2,
                    media_type=DetectedMediaType.PDF,
                    source_sha256=SECOND_SOURCE_SHA256,
                ),
                CaseSourcePreflightFact(
                    source_id=IMAGE_SOURCE_ID,
                    position=3,
                    media_type=DetectedMediaType.PNG,
                    source_sha256=IMAGE_SHA256,
                ),
            ),
            active_page_ids=(PAGE_ID, SECOND_PAGE_ID),
        ),
        TemplatePreflightFacts(
            case_id=CASE_ID,
            manifest_revision=MANIFEST_REVISION,
            availability=TemplateFactAvailability.CURRENT,
            binding=_template_binding(),
            notices=(),
        ),
        ChecklistPreflightFacts(
            case_id=CASE_ID,
            manifest_revision=MANIFEST_REVISION,
            availability=ChecklistFactAvailability.CURRENT,
            definition=definition,
            projection=_projection(definition),
        ),
        SourceInspectionsPreflightFacts(
            case_id=CASE_ID,
            manifest_revision=MANIFEST_REVISION,
            inspections=(
                SourceInspectionFact(
                    source_id=PDF_SOURCE_ID,
                    position=1,
                    media_type=DetectedMediaType.PDF,
                    availability=SourceInspectionAvailability.CURRENT,
                    source_sha256=SOURCE_SHA256,
                    pdf_result=_pdf_result(),
                ),
                SourceInspectionFact(
                    source_id=UNAVAILABLE_PDF_SOURCE_ID,
                    position=2,
                    media_type=DetectedMediaType.PDF,
                    availability=SourceInspectionAvailability.UNAVAILABLE,
                    source_sha256=SECOND_SOURCE_SHA256,
                ),
                SourceInspectionFact(
                    source_id=IMAGE_SOURCE_ID,
                    position=3,
                    media_type=DetectedMediaType.PNG,
                    availability=SourceInspectionAvailability.NOT_APPLICABLE,
                    source_sha256=IMAGE_SHA256,
                ),
            ),
        ),
        QualityPreflightFacts(
            case_id=CASE_ID,
            manifest_revision=MANIFEST_REVISION,
            availability=QualityFactAvailability.CURRENT,
            page_ids=(PAGE_ID, SECOND_PAGE_ID),
            findings=(_quality_finding(),),
            profile_id="balanced.v1",
            profile_sha256=PROFILE_SHA256,
        ),
        OutputPlanPreflightFacts(
            case_id=CASE_ID,
            manifest_revision=MANIFEST_REVISION,
            availability=OutputPlanAvailability.CURRENT,
            output_mode="single.packet",
            items=(
                OutputItemPreflightFact(
                    output_key=OUTPUT_KEY,
                    position=1,
                    page_ids=(PAGE_ID, SECOND_PAGE_ID),
                    predicted_bytes=12_345,
                ),
            ),
        ),
    )


def _authority(
    *,
    case_id: UUID = CASE_ID,
    manifest_revision: int = MANIFEST_REVISION,
    manifest_sha256: str = MANIFEST_SHA256,
    template_binding: TemplateBinding = TEMPLATE_BINDING,
) -> PreflightAuthority:
    scopes = (
        PreflightScope(PreflightScopeKind.CASE, case_id),
        PreflightScope(PreflightScopeKind.REQUIREMENT, REQUIREMENT_ID, 1),
        PreflightScope(PreflightScopeKind.SOURCE, PDF_SOURCE_ID, 1),
        PreflightScope(PreflightScopeKind.SOURCE, UNAVAILABLE_PDF_SOURCE_ID, 2),
        PreflightScope(PreflightScopeKind.SOURCE, IMAGE_SOURCE_ID, 3),
        PreflightScope(PreflightScopeKind.PAGE, PAGE_ID, 1),
        PreflightScope(PreflightScopeKind.PAGE, SECOND_PAGE_ID, 2),
        PreflightScope(PreflightScopeKind.OUTPUT, OUTPUT_KEY, 1),
    )
    return PreflightAuthority(
        case_id=case_id,
        manifest_revision=manifest_revision,
        manifest_sha256=manifest_sha256,
        template_binding=template_binding,
        scope_catalog=PreflightScopeCatalog(case_id=case_id, scopes=scopes),
    )


def _input_map(
    facts: tuple[PreflightFactValue, ...],
    authority: PreflightAuthority,
) -> dict[PreflightDependency, PreflightInput]:
    return {
        dependency: PreflightInput(authority, dependency, value)
        for dependency, value in zip(PreflightDependency, facts, strict=True)
    }


def _assert_canonical_binding_error(
    facts: tuple[PreflightFactValue, ...],
    expected_context: str,
) -> None:
    authority = _authority()
    with pytest.raises(PreflightError) as captured:
        validate_canonical_preflight_bindings(_input_map(facts, authority), authority)

    assert captured.value.code is PreflightErrorCode.MODEL_INVALID
    assert captured.value.context == expected_context


def test_six_canonical_fact_dtos_bind_to_exact_authority() -> None:
    facts: tuple[PreflightFactValue, ...] = _canonical_facts()
    authority = _authority()

    input_by_dependency = _input_map(facts, authority)
    inputs = tuple(input_by_dependency.values())
    validate_canonical_preflight_bindings(input_by_dependency, authority)

    assert tuple(type(item.value) for item in inputs) == (
        CasePreflightFacts,
        TemplatePreflightFacts,
        ChecklistPreflightFacts,
        SourceInspectionsPreflightFacts,
        QualityPreflightFacts,
        OutputPlanPreflightFacts,
    )
    assert all(item.authority is authority for item in inputs)
    assert all(len(item.revision_sha256) == 64 for item in inputs)


@pytest.mark.parametrize(
    ("dependency", "fact_index"),
    tuple(
        (dependency, index) for index, dependency in enumerate(tuple(PreflightDependency)[1:], 1)
    ),
)
def test_canonical_non_case_fact_requires_authoritative_case_projection(
    dependency: PreflightDependency,
    fact_index: int,
) -> None:
    authority = _authority()
    selected = _canonical_facts()[fact_index]
    inputs = {dependency: PreflightInput(authority, dependency, selected)}

    with pytest.raises(PreflightError) as captured:
        validate_canonical_preflight_bindings(inputs, authority)

    assert captured.value.code is PreflightErrorCode.MODEL_INVALID
    assert captured.value.context == "preflight.binding_case"


@pytest.mark.parametrize(
    ("fact_index", "wrong_dependency"),
    (
        (0, PreflightDependency.TEMPLATE),
        (1, PreflightDependency.CHECKLIST),
        (2, PreflightDependency.SOURCE_INSPECTIONS),
        (3, PreflightDependency.QUALITY_FINDINGS),
        (4, PreflightDependency.OUTPUT_PLAN),
        (5, PreflightDependency.CASE),
    ),
)
def test_canonical_fact_rejects_wrong_dependency_slot(
    fact_index: int,
    wrong_dependency: PreflightDependency,
) -> None:
    authority = _authority()
    selected = _canonical_facts()[fact_index]
    inputs = {
        wrong_dependency: PreflightInput(authority, wrong_dependency, selected),
    }

    with pytest.raises(PreflightError) as captured:
        validate_canonical_preflight_bindings(inputs, authority)

    assert captured.value.code is PreflightErrorCode.MODEL_INVALID
    assert captured.value.context == "preflight.binding_dependency"


def test_canonical_binding_rejects_changed_source_digest() -> None:
    case, template, checklist, inspections, quality, output = _canonical_facts()
    first = inspections.inspections[0]
    changed_inspections = SourceInspectionsPreflightFacts(
        case_id=CASE_ID,
        manifest_revision=MANIFEST_REVISION,
        inspections=(
            SourceInspectionFact(
                source_id=first.source_id,
                position=first.position,
                media_type=first.media_type,
                availability=first.availability,
                source_sha256="e" * 64,
                pdf_result=first.pdf_result,
            ),
            *inspections.inspections[1:],
        ),
    )

    _assert_canonical_binding_error(
        (case, template, checklist, changed_inspections, quality, output),
        "preflight.binding_sources",
    )


def test_canonical_binding_rejects_checklist_page_order_from_another_revision() -> None:
    case, template, checklist, inspections, quality, output = _canonical_facts()
    assert checklist.definition is not None
    changed_checklist = ChecklistPreflightFacts(
        case_id=CASE_ID,
        manifest_revision=MANIFEST_REVISION,
        availability=ChecklistFactAvailability.CURRENT,
        definition=checklist.definition,
        projection=_projection(
            checklist.definition,
            page_ids=(SECOND_PAGE_ID, PAGE_ID),
        ),
    )

    _assert_canonical_binding_error(
        (case, template, changed_checklist, inspections, quality, output),
        "preflight.binding_pages",
    )


def test_canonical_binding_rejects_quality_page_order_from_another_revision() -> None:
    case, template, checklist, inspections, quality, output = _canonical_facts()
    changed_quality = QualityPreflightFacts(
        case_id=CASE_ID,
        manifest_revision=MANIFEST_REVISION,
        availability=QualityFactAvailability.CURRENT,
        page_ids=(SECOND_PAGE_ID, PAGE_ID),
        findings=(_quality_finding(page_position=2),),
        profile_id=quality.profile_id,
        profile_sha256=quality.profile_sha256,
    )

    _assert_canonical_binding_error(
        (case, template, checklist, inspections, changed_quality, output),
        "preflight.binding_pages",
    )


def test_canonical_binding_rejects_output_page_outside_active_inventory() -> None:
    case, template, checklist, inspections, quality, output = _canonical_facts()
    changed_output = OutputPlanPreflightFacts(
        case_id=CASE_ID,
        manifest_revision=MANIFEST_REVISION,
        availability=OutputPlanAvailability.CURRENT,
        output_mode=output.output_mode,
        items=(
            OutputItemPreflightFact(
                output_key=OUTPUT_KEY,
                position=1,
                page_ids=(PAGE_ID, OUTSIDE_PAGE_ID),
                predicted_bytes=12_345,
            ),
        ),
    )

    _assert_canonical_binding_error(
        (case, template, checklist, inspections, quality, changed_output),
        "preflight.binding_outputs",
    )


def test_canonical_binding_rejects_template_outside_authority() -> None:
    case, _, checklist, inspections, quality, output = _canonical_facts()
    changed_template = TemplatePreflightFacts(
        case_id=CASE_ID,
        manifest_revision=MANIFEST_REVISION,
        availability=TemplateFactAvailability.CURRENT,
        binding=TemplateBinding(
            template_id="synthetic.other",
            template_version="1.0.0",
            sha256="b" * 64,
        ),
        notices=(),
    )

    _assert_canonical_binding_error(
        (case, changed_template, checklist, inspections, quality, output),
        "preflight.binding_template",
    )


@pytest.mark.parametrize(
    "authority",
    (
        _authority(case_id=OTHER_CASE_ID),
        _authority(manifest_revision=MANIFEST_REVISION + 1),
    ),
)
def test_preflight_input_rejects_wrong_case_or_revision_authority(
    authority: PreflightAuthority,
) -> None:
    facts = _canonical_facts()[0]

    with pytest.raises(PreflightError) as captured:
        PreflightInput(
            authority=authority,
            dependency=PreflightDependency.CASE,
            value=facts,
        )

    assert captured.value.code is PreflightErrorCode.MODEL_INVALID
    assert captured.value.context == "preflight.input_binding"


def test_manifest_and_template_authority_participate_in_input_identity() -> None:
    facts = _canonical_facts()[0]
    first = PreflightInput(_authority(), PreflightDependency.CASE, facts)
    changed_manifest = PreflightInput(
        _authority(manifest_sha256="f" * 64),
        PreflightDependency.CASE,
        facts,
    )
    changed_template = PreflightInput(
        _authority(
            template_binding=TemplateBinding(
                template_id="synthetic.other",
                template_version="1.0.0",
                sha256="b" * 64,
            )
        ),
        PreflightDependency.CASE,
        facts,
    )

    assert first.revision_sha256 != changed_manifest.revision_sha256
    assert first.revision_sha256 != changed_template.revision_sha256
    assert (
        first.revision_sha256
        == PreflightInput(
            _authority(),
            PreflightDependency.CASE,
            facts,
        ).revision_sha256
    )


def test_pdf_inspection_fact_retains_full_result_and_explicit_absence_states() -> None:
    facts = _canonical_facts()[3]
    current, unavailable, image = facts.inspections
    expected = _pdf_result()

    assert current.availability is SourceInspectionAvailability.CURRENT
    assert current.pdf_result == expected
    assert current.pdf_result is not None
    assert current.pdf_result.features.values == expected.features.values
    assert unavailable.availability is SourceInspectionAvailability.UNAVAILABLE
    assert unavailable.pdf_result is None
    assert image.availability is SourceInspectionAvailability.NOT_APPLICABLE
    assert image.pdf_result is None


def test_case_page_details_bind_geometry_and_overlay_counts_to_active_page_order() -> None:
    page_details = (
        CasePagePreflightFact(
            page_id=PAGE_ID,
            position=1,
            source_id=PDF_SOURCE_ID,
            source_page_number=1,
            width_millipoints=612_000,
            height_millipoints=792_000,
            overlay_count=2,
            unapplied_overlay_count=1,
            ordinary_shape_count=1,
        ),
        CasePagePreflightFact(
            page_id=SECOND_PAGE_ID,
            position=2,
            source_id=IMAGE_SOURCE_ID,
            source_page_number=1,
            width_millipoints=595_000,
            height_millipoints=842_000,
            overlay_count=0,
            unapplied_overlay_count=0,
            ordinary_shape_count=0,
        ),
    )
    facts = CasePreflightFacts(
        case_id=CASE_ID,
        manifest_revision=MANIFEST_REVISION,
        template_binding=_template_binding(),
        sources=_canonical_facts()[0].sources,
        active_page_ids=(PAGE_ID, SECOND_PAGE_ID),
        page_details_availability=CasePageDetailsAvailability.CURRENT,
        page_details=page_details,
    )

    assert facts.page_details == page_details
    assert "612000" not in repr(facts)
    assert "height" not in repr(page_details[0]).casefold()


@pytest.mark.parametrize(
    "page_details",
    (
        (
            CasePagePreflightFact(
                PAGE_ID,
                1,
                PDF_SOURCE_ID,
                1,
                612_000,
                792_000,
                1,
                0,
                0,
            ),
        ),
        (
            CasePagePreflightFact(
                SECOND_PAGE_ID,
                1,
                PDF_SOURCE_ID,
                1,
                612_000,
                792_000,
                1,
                0,
                0,
            ),
            CasePagePreflightFact(
                PAGE_ID,
                2,
                PDF_SOURCE_ID,
                2,
                612_000,
                792_000,
                0,
                0,
                0,
            ),
        ),
    ),
)
def test_current_case_page_details_require_complete_exact_active_page_binding(
    page_details: tuple[CasePagePreflightFact, ...],
) -> None:
    with pytest.raises(PreflightError) as captured:
        CasePreflightFacts(
            case_id=CASE_ID,
            manifest_revision=MANIFEST_REVISION,
            template_binding=_template_binding(),
            sources=_canonical_facts()[0].sources,
            active_page_ids=(PAGE_ID, SECOND_PAGE_ID),
            page_details_availability=CasePageDetailsAvailability.CURRENT,
            page_details=page_details,
        )

    assert captured.value.code is PreflightErrorCode.MODEL_INVALID
    assert captured.value.context == "preflight.case_page_details"


def test_case_page_detail_change_participates_in_canonical_input_identity() -> None:
    def case_with_width(width_millipoints: int) -> CasePreflightFacts:
        original = _canonical_facts()[0]
        return CasePreflightFacts(
            case_id=original.case_id,
            manifest_revision=original.manifest_revision,
            template_binding=original.template_binding,
            sources=original.sources,
            active_page_ids=original.active_page_ids,
            page_details_availability=CasePageDetailsAvailability.CURRENT,
            page_details=(
                CasePagePreflightFact(
                    PAGE_ID,
                    1,
                    PDF_SOURCE_ID,
                    1,
                    width_millipoints,
                    792_000,
                    0,
                    0,
                    0,
                ),
                CasePagePreflightFact(
                    SECOND_PAGE_ID,
                    2,
                    IMAGE_SOURCE_ID,
                    1,
                    595_000,
                    842_000,
                    0,
                    0,
                    0,
                ),
            ),
        )

    first = PreflightInput(_authority(), PreflightDependency.CASE, case_with_width(612_000))
    changed = PreflightInput(_authority(), PreflightDependency.CASE, case_with_width(613_000))

    assert first.revision_sha256 != changed.revision_sha256


def test_template_checklist_quality_and_output_absence_states_are_explicit() -> None:
    not_selected = TemplatePreflightFacts(
        CASE_ID,
        MANIFEST_REVISION,
        TemplateFactAvailability.NOT_SELECTED,
        None,
        (),
    )
    template_unavailable = TemplatePreflightFacts(
        CASE_ID,
        MANIFEST_REVISION,
        TemplateFactAvailability.UNAVAILABLE,
        TEMPLATE_BINDING,
        (),
    )
    checklist_unavailable = ChecklistPreflightFacts(
        CASE_ID,
        MANIFEST_REVISION,
        ChecklistFactAvailability.UNAVAILABLE,
        None,
        None,
    )
    quality_unavailable = QualityPreflightFacts(
        CASE_ID,
        MANIFEST_REVISION,
        QualityFactAvailability.UNAVAILABLE,
        (PAGE_ID, SECOND_PAGE_ID),
        (),
        None,
        None,
    )
    output_unavailable = OutputPlanPreflightFacts(
        CASE_ID,
        MANIFEST_REVISION,
        OutputPlanAvailability.UNAVAILABLE,
        "unplanned",
        (),
    )

    assert not_selected.binding is None
    assert template_unavailable.binding == TEMPLATE_BINDING
    assert checklist_unavailable.projection is None
    assert quality_unavailable.profile_id is None
    assert output_unavailable.items == ()

    with pytest.raises(PreflightError):
        QualityPreflightFacts(
            CASE_ID,
            MANIFEST_REVISION,
            QualityFactAvailability.UNAVAILABLE,
            (PAGE_ID, SECOND_PAGE_ID),
            (),
            "scan-quality-v1",
            PROFILE_SHA256,
        )


@pytest.mark.parametrize(
    ("media_type", "availability"),
    (
        (DetectedMediaType.PDF, SourceInspectionAvailability.NOT_APPLICABLE),
        (DetectedMediaType.JPEG, SourceInspectionAvailability.UNAVAILABLE),
    ),
)
def test_pdf_and_image_inspections_require_explicit_applicability_state(
    media_type: DetectedMediaType,
    availability: SourceInspectionAvailability,
) -> None:
    with pytest.raises(PreflightError) as captured:
        SourceInspectionFact(
            source_id=PDF_SOURCE_ID,
            position=1,
            media_type=media_type,
            availability=availability,
            source_sha256=SOURCE_SHA256,
        )

    assert captured.value.code is PreflightErrorCode.MODEL_INVALID


@pytest.mark.parametrize(
    ("media_type", "saved_state"),
    (
        (DetectedMediaType.PDF, SignedSourceSignatureState.NOT_APPLICABLE),
        (DetectedMediaType.PNG, SignedSourceSignatureState.UNKNOWN),
        (DetectedMediaType.JPEG, SignedSourceSignatureState.MARKERS_DETECTED),
    ),
)
def test_saved_signature_state_must_match_source_media_type(
    media_type: DetectedMediaType,
    saved_state: SignedSourceSignatureState,
) -> None:
    with pytest.raises(PreflightError) as case_error:
        CaseSourcePreflightFact(PDF_SOURCE_ID, 1, media_type, SOURCE_SHA256, saved_state)
    with pytest.raises(PreflightError) as inspection_error:
        SourceInspectionFact(
            PDF_SOURCE_ID,
            1,
            media_type,
            SourceInspectionAvailability.UNAVAILABLE
            if media_type is DetectedMediaType.PDF
            else SourceInspectionAvailability.NOT_APPLICABLE,
            SOURCE_SHA256,
            saved_signature_state=saved_state,
        )
    assert case_error.value.context == "preflight.saved_signature_state"
    assert inspection_error.value.context == "preflight.saved_signature_state"


def test_canonical_binding_rejects_inspection_with_different_saved_signature_state() -> None:
    case, template, checklist, inspections, quality, output = _canonical_facts()
    changed = replace(
        inspections,
        inspections=(
            replace(
                inspections.inspections[0],
                saved_signature_state=SignedSourceSignatureState.MARKERS_DETECTED,
            ),
            *inspections.inspections[1:],
        ),
    )
    _assert_canonical_binding_error(
        (case, template, checklist, changed, quality, output), "preflight.binding_sources"
    )


def test_template_notice_accepts_maximum_inert_text_without_callable_logic() -> None:
    message = "A" * 4_096
    notice = TemplateNoticeFact(
        notice_id="review.statement",
        kind=TemplateNoticeKind.NEEDS_REVIEW,
        position=1,
        message=message,
        requirement_id=REQUIREMENT_ID,
    )
    facts = TemplatePreflightFacts(
        case_id=CASE_ID,
        manifest_revision=MANIFEST_REVISION,
        availability=TemplateFactAvailability.CURRENT,
        binding=_template_binding(),
        notices=(notice,),
    )

    assert facts.notices[0].message == message
    assert len(facts.notices[0].message) == 4_096
    assert not callable(facts.notices[0].message)


def test_output_item_has_deterministic_internal_key_and_no_filename_or_path() -> None:
    facts = _canonical_facts()[5]
    item = facts.items[0]
    duplicate = OutputPlanPreflightFacts(
        case_id=facts.case_id,
        manifest_revision=facts.manifest_revision,
        availability=facts.availability,
        output_mode=facts.output_mode,
        items=(
            OutputItemPreflightFact(
                output_key=OUTPUT_KEY,
                position=1,
                page_ids=(PAGE_ID, SECOND_PAGE_ID),
                predicted_bytes=12_345,
            ),
        ),
    )

    assert item.output_key == OUTPUT_KEY
    assert item == duplicate.items[0]
    assert (
        PreflightInput(
            _authority(),
            PreflightDependency.OUTPUT_PLAN,
            facts,
        ).revision_sha256
        == PreflightInput(
            _authority(),
            PreflightDependency.OUTPUT_PLAN,
            duplicate,
        ).revision_sha256
    )
    assert {field.name for field in fields(OutputItemPreflightFact)} == {
        "output_key",
        "position",
        "page_ids",
        "predicted_bytes",
    }
    assert "filename" not in repr(item).casefold()
    assert "path" not in repr(item).casefold()


def test_output_plan_rejects_repeated_pages_across_items() -> None:
    with pytest.raises(PreflightError) as captured:
        OutputPlanPreflightFacts(
            CASE_ID,
            MANIFEST_REVISION,
            OutputPlanAvailability.CURRENT,
            "separate-pdfs",
            (
                OutputItemPreflightFact("packet-one", 1, (PAGE_ID,)),
                OutputItemPreflightFact("packet-two", 2, (PAGE_ID,)),
            ),
        )

    assert captured.value.code is PreflightErrorCode.MODEL_INVALID
    assert captured.value.context == "preflight.output_items"


def test_output_plan_binds_flattened_overlay_pages_to_exact_partition_order() -> None:
    output = OutputPlanPreflightFacts(
        CASE_ID,
        MANIFEST_REVISION,
        OutputPlanAvailability.CURRENT,
        "combined_pdf",
        (
            OutputItemPreflightFact(
                OUTPUT_KEY,
                1,
                (PAGE_ID, SECOND_PAGE_ID),
                12_345,
            ),
        ),
        OutputOverlayFlatteningAvailability.CURRENT,
        (PAGE_ID,),
    )

    assert output.flattened_overlay_page_ids == (PAGE_ID,)
    assert "page_id" not in repr(output).casefold()

    for invalid_pages in ((SECOND_PAGE_ID, PAGE_ID), (OUTSIDE_PAGE_ID,)):
        with pytest.raises(PreflightError) as captured:
            OutputPlanPreflightFacts(
                CASE_ID,
                MANIFEST_REVISION,
                OutputPlanAvailability.CURRENT,
                "combined_pdf",
                output.items,
                OutputOverlayFlatteningAvailability.CURRENT,
                invalid_pages,
            )
        assert captured.value.context == "preflight.output_overlay_pages"


def test_unavailable_overlay_flattening_rejects_asserted_page_evidence() -> None:
    with pytest.raises(PreflightError) as captured:
        OutputPlanPreflightFacts(
            CASE_ID,
            MANIFEST_REVISION,
            OutputPlanAvailability.CURRENT,
            "combined_pdf",
            (
                OutputItemPreflightFact(
                    OUTPUT_KEY,
                    1,
                    (PAGE_ID, SECOND_PAGE_ID),
                    12_345,
                ),
            ),
            OutputOverlayFlatteningAvailability.UNAVAILABLE,
            (PAGE_ID,),
        )

    assert captured.value.code is PreflightErrorCode.MODEL_INVALID
    assert captured.value.context == "preflight.output_overlay_pages"


def test_maximum_partitioned_output_plan_is_wrappable_and_canonically_bound() -> None:
    def generated_uuid(position: int) -> UUID:
        return UUID(int=(9 << 96) | (4 << 76) | (2 << 62) | position)

    page_ids = tuple(generated_uuid(position) for position in range(1, 4_097))
    items = tuple(
        OutputItemPreflightFact(f"packet-{position}", position, (page_id,))
        for position, page_id in enumerate(page_ids, start=1)
    )
    output = OutputPlanPreflightFacts(
        CASE_ID,
        MANIFEST_REVISION,
        OutputPlanAvailability.CURRENT,
        "separate-pdfs",
        items,
    )
    case = CasePreflightFacts(
        CASE_ID,
        MANIFEST_REVISION,
        None,
        (),
        page_ids,
    )
    authority = PreflightAuthority(
        CASE_ID,
        MANIFEST_REVISION,
        MANIFEST_SHA256,
        None,
        PreflightScopeCatalog(
            CASE_ID,
            (
                PreflightScope(PreflightScopeKind.CASE, CASE_ID),
                *(
                    PreflightScope(PreflightScopeKind.PAGE, page_id, position)
                    for position, page_id in enumerate(page_ids, start=1)
                ),
                *(
                    PreflightScope(
                        PreflightScopeKind.OUTPUT,
                        item.output_key,
                        item.position,
                    )
                    for item in items
                ),
            ),
        ),
    )
    inputs = {
        PreflightDependency.CASE: PreflightInput(
            authority,
            PreflightDependency.CASE,
            case,
        ),
        PreflightDependency.OUTPUT_PLAN: PreflightInput(
            authority,
            PreflightDependency.OUTPUT_PLAN,
            output,
        ),
    }

    validate_canonical_preflight_bindings(inputs, authority)

    assert len(output.items) == 4_096
    assert all(len(item.revision_sha256) == 64 for item in inputs.values())


def test_checklist_facts_reject_projection_from_another_definition() -> None:
    expected = _definition()
    other = ChecklistDefinition(
        binding=_template_binding(),
        requirements=(
            ChecklistRequirement(
                requirement_id="supporting",
                count_unit=RequirementCountUnit.PAGES,
                minimum_count=1,
                maximum_count=3,
            ),
        ),
    )

    with pytest.raises(PreflightError) as captured:
        ChecklistPreflightFacts(
            case_id=CASE_ID,
            manifest_revision=MANIFEST_REVISION,
            availability=ChecklistFactAvailability.CURRENT,
            definition=expected,
            projection=_projection(other),
        )

    assert captured.value.code is PreflightErrorCode.MODEL_INVALID
    assert captured.value.context == "preflight.checklist_facts"


def test_quality_facts_require_exact_page_order_binding() -> None:
    with pytest.raises(PreflightError) as captured:
        QualityPreflightFacts(
            case_id=CASE_ID,
            manifest_revision=MANIFEST_REVISION,
            availability=QualityFactAvailability.CURRENT,
            page_ids=(PAGE_ID, SECOND_PAGE_ID),
            findings=(_quality_finding(page_position=2),),
            profile_id="balanced.v1",
            profile_sha256=PROFILE_SHA256,
        )

    assert captured.value.code is PreflightErrorCode.MODEL_INVALID
    assert captured.value.context == "preflight.quality_binding"

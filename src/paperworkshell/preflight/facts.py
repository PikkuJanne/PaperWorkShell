"""Minimal immutable fact projections accepted by preflight rules.

These models intentionally exclude display names, paths, OCR text, document
text, metadata values, thumbnails, proposed filenames, and raw exceptions.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final
from uuid import UUID

from paperworkshell.domain.checklist import (
    ChecklistDefinition,
    ChecklistProjection,
    TemplateBinding,
)
from paperworkshell.domain.import_precheck import DetectedMediaType
from paperworkshell.domain.pdf_inspection import PdfInspectionResult
from paperworkshell.domain.quality_analysis import (
    MAX_QUALITY_FINDINGS_PER_PAGE,
    QualityFinding,
    quality_finding_sort_key,
)
from paperworkshell.domain.signed_source_policy import SignedSourceSignatureState
from paperworkshell.preflight.models import (
    MAX_PREFLIGHT_FACT_STRING_CODEPOINTS,
    MAX_PREFLIGHT_MESSAGE_CODEPOINTS,
    MAX_PREFLIGHT_STATEMENT_VERSION,
    PreflightAuthority,
    PreflightDependency,
    PreflightErrorCode,
    PreflightFactValue,
    PreflightInput,
    PreflightScope,
    PreflightScopeKind,
    _require_presentation,
    _require_sha256,
    _require_uuid4,
    reject_preflight,
)

_TOKEN: Final = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")
_PROFILE_TOKEN: Final = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")
MAX_PREFLIGHT_SOURCES: Final = 4_096
MAX_PREFLIGHT_PAGES: Final = 4_096
MAX_PREFLIGHT_TEMPLATE_NOTICES: Final = 512
MAX_PREFLIGHT_OUTPUTS: Final = 4_096


@dataclass(frozen=True, slots=True, repr=False)
class CaseSourcePreflightFact:
    """One immutable source identity without a name, path, or metadata value."""

    source_id: UUID
    position: int
    media_type: DetectedMediaType
    source_sha256: str = field(repr=False)
    saved_signature_state: SignedSourceSignatureState | None = None

    def __post_init__(self) -> None:
        _require_uuid4(self.source_id, "preflight.case_source")
        _require_sha256(self.source_sha256, "preflight.case_source_digest")
        if (
            type(self.position) is not int
            or not 1 <= self.position <= MAX_PREFLIGHT_SOURCES
            or type(self.media_type) is not DetectedMediaType
        ):
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.case_source")
        _require_saved_signature_state(self.media_type, self.saved_signature_state)

    def __repr__(self) -> str:
        return (
            "CaseSourcePreflightFact("
            f"position={self.position!r}, media_type={self.media_type.value!r})"
        )


class CasePageDetailsAvailability(StrEnum):
    """Whether current geometry and overlay projections cover every active page."""

    CURRENT = "current"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True, repr=False)
class CasePagePreflightFact:
    """One active page's content-free geometry and visual-intent summary."""

    page_id: UUID
    position: int
    source_id: UUID
    source_page_number: int
    width_millipoints: int
    height_millipoints: int
    overlay_count: int
    unapplied_overlay_count: int
    ordinary_shape_count: int

    def __post_init__(self) -> None:
        _require_uuid4(self.page_id, "preflight.case_page")
        _require_uuid4(self.source_id, "preflight.case_page_source")
        numeric_values = (
            self.position,
            self.source_page_number,
            self.width_millipoints,
            self.height_millipoints,
            self.overlay_count,
            self.unapplied_overlay_count,
            self.ordinary_shape_count,
        )
        if (
            self.page_id == self.source_id
            or any(type(value) is not int for value in numeric_values)
            or not 1 <= self.position <= MAX_PREFLIGHT_PAGES
            or not 1 <= self.source_page_number <= MAX_PREFLIGHT_PAGES
            or not 1 <= self.width_millipoints <= MAX_PREFLIGHT_STATEMENT_VERSION
            or not 1 <= self.height_millipoints <= MAX_PREFLIGHT_STATEMENT_VERSION
            or not 0 <= self.overlay_count <= MAX_PREFLIGHT_PAGES
            or not 0 <= self.unapplied_overlay_count <= self.overlay_count
            or not 0 <= self.ordinary_shape_count <= self.overlay_count
        ):
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.case_page")

    def __repr__(self) -> str:
        return (
            "CasePagePreflightFact("
            f"position={self.position!r}, source_page_number={self.source_page_number!r}, "
            f"overlay_count={self.overlay_count!r}, "
            f"unapplied_overlay_count={self.unapplied_overlay_count!r}, "
            f"ordinary_shape_count={self.ordinary_shape_count!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class CasePreflightFacts(PreflightFactValue):
    """Authoritative case inventory needed to correlate other fact groups."""

    case_id: UUID
    manifest_revision: int
    template_binding: TemplateBinding | None
    sources: tuple[CaseSourcePreflightFact, ...] = field(repr=False)
    active_page_ids: tuple[UUID, ...] = field(repr=False)
    page_details_availability: CasePageDetailsAvailability = CasePageDetailsAvailability.UNAVAILABLE
    page_details: tuple[CasePagePreflightFact, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        _require_case_revision(self.case_id, self.manifest_revision, "preflight.case")
        if self.template_binding is not None and type(self.template_binding) is not TemplateBinding:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.case_template")
        if (
            type(self.sources) is not tuple
            or len(self.sources) > MAX_PREFLIGHT_SOURCES
            or any(type(item) is not CaseSourcePreflightFact for item in self.sources)
            or tuple(item.position for item in self.sources)
            != tuple(range(1, len(self.sources) + 1))
            or len({item.source_id for item in self.sources}) != len(self.sources)
        ):
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.case_sources")
        _require_uuid_inventory(
            self.active_page_ids,
            maximum=MAX_PREFLIGHT_PAGES,
            allow_empty=True,
            context="preflight.case_pages",
        )
        if self.case_id in self.source_ids or self.case_id in self.active_page_ids:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.case_namespace")
        if (
            type(self.page_details_availability) is not CasePageDetailsAvailability
            or type(self.page_details) is not tuple
            or len(self.page_details) > MAX_PREFLIGHT_PAGES
            or any(type(item) is not CasePagePreflightFact for item in self.page_details)
        ):
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.case_page_details")
        if self.page_details_availability is CasePageDetailsAvailability.CURRENT:
            if (
                tuple(item.page_id for item in self.page_details) != self.active_page_ids
                or tuple(item.position for item in self.page_details)
                != tuple(range(1, len(self.page_details) + 1))
                or any(item.source_id not in self.source_ids for item in self.page_details)
            ):
                reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.case_page_details")
        elif self.page_details:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.case_page_details")

    @property
    def source_ids(self) -> tuple[UUID, ...]:
        return tuple(item.source_id for item in self.sources)

    def __repr__(self) -> str:
        return (
            "CasePreflightFacts("
            f"manifest_revision={self.manifest_revision!r}, "
            f"source_count={len(self.sources)}, page_count={len(self.active_page_ids)}, "
            f"page_details_availability={self.page_details_availability.value!r})"
        )


class TemplateNoticeKind(StrEnum):
    """Exact declarative notice classes retained without executing template logic."""

    INFORMATION = "information"
    NEEDS_REVIEW = "needs_review"
    CONFIRMATION = "confirmation"


class TemplateFactAvailability(StrEnum):
    """Whether the exact selected template declarations are available."""

    CURRENT = "current"
    NOT_SELECTED = "not_selected"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True, repr=False)
class TemplateNoticeFact:
    """One validated inert warning or review statement from the bound template."""

    notice_id: str
    kind: TemplateNoticeKind
    position: int
    message: str = field(repr=False)
    requirement_id: str | None = None

    def __post_init__(self) -> None:
        _require_token(self.notice_id, "preflight.template_notice")
        if (
            type(self.kind) is not TemplateNoticeKind
            or type(self.position) is not int
            or not 1 <= self.position <= MAX_PREFLIGHT_TEMPLATE_NOTICES
        ):
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.template_notice")
        _require_presentation(
            self.message,
            "preflight.template_notice_message",
            maximum_codepoints=MAX_PREFLIGHT_MESSAGE_CODEPOINTS,
        )
        if self.requirement_id is not None:
            _require_token(self.requirement_id, "preflight.template_notice_requirement")

    def __repr__(self) -> str:
        return (
            "TemplateNoticeFact("
            f"kind={self.kind.value!r}, position={self.position!r}, "
            f"has_requirement_scope={self.requirement_id is not None})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class TemplatePreflightFacts(PreflightFactValue):
    """Exact template binding plus inert, already validated review notices."""

    case_id: UUID
    manifest_revision: int
    availability: TemplateFactAvailability
    binding: TemplateBinding | None
    notices: tuple[TemplateNoticeFact, ...] = field(repr=False)

    def __post_init__(self) -> None:
        _require_case_revision(self.case_id, self.manifest_revision, "preflight.template")
        if (
            type(self.availability) is not TemplateFactAvailability
            or type(self.notices) is not tuple
            or len(self.notices) > MAX_PREFLIGHT_TEMPLATE_NOTICES
            or any(type(item) is not TemplateNoticeFact for item in self.notices)
            or tuple(item.position for item in self.notices)
            != tuple(range(1, len(self.notices) + 1))
            or len({item.notice_id for item in self.notices}) != len(self.notices)
        ):
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.template_facts")
        if self.availability is TemplateFactAvailability.CURRENT:
            if type(self.binding) is not TemplateBinding:
                reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.template_binding")
        elif self.availability is TemplateFactAvailability.NOT_SELECTED:
            if self.binding is not None or self.notices:
                reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.template_binding")
        elif type(self.binding) is not TemplateBinding or self.notices:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.template_binding")

    def __repr__(self) -> str:
        return (
            "TemplatePreflightFacts("
            f"manifest_revision={self.manifest_revision!r}, "
            f"availability={self.availability.value!r}, notice_count={len(self.notices)})"
        )


class ChecklistFactAvailability(StrEnum):
    """Whether a current saved checklist projection could be constructed."""

    CURRENT = "current"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True, repr=False)
class ChecklistPreflightFacts(PreflightFactValue):
    """Exact pure checklist definition and projection, without GUI text."""

    case_id: UUID
    manifest_revision: int
    availability: ChecklistFactAvailability
    definition: ChecklistDefinition | None = field(repr=False)
    projection: ChecklistProjection | None = field(repr=False)

    def __post_init__(self) -> None:
        _require_case_revision(self.case_id, self.manifest_revision, "preflight.checklist")
        if type(self.availability) is not ChecklistFactAvailability:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.checklist_facts")
        if self.availability is ChecklistFactAvailability.CURRENT:
            if (
                type(self.definition) is not ChecklistDefinition
                or type(self.projection) is not ChecklistProjection
                or tuple(item.requirement for item in self.projection.requirements)
                != self.definition.requirements
            ):
                reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.checklist_facts")
        elif self.definition is not None or self.projection is not None:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.checklist_facts")

    def __repr__(self) -> str:
        requirement_count = 0 if self.projection is None else len(self.projection.requirements)
        active_page_count = 0 if self.projection is None else len(self.projection.active_page_ids)
        return (
            "ChecklistPreflightFacts("
            f"manifest_revision={self.manifest_revision!r}, "
            f"availability={self.availability.value!r}, "
            f"requirement_count={requirement_count}, active_page_count={active_page_count})"
        )


class SourceInspectionAvailability(StrEnum):
    """Whether a current full structural result exists for the source."""

    CURRENT = "current"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True, repr=False)
class SourceInspectionFact:
    """Full path-free PDF inspection or an explicit unverified/applicability state."""

    source_id: UUID
    position: int
    media_type: DetectedMediaType
    availability: SourceInspectionAvailability
    source_sha256: str = field(repr=False)
    pdf_result: PdfInspectionResult | None = field(default=None, repr=False)
    saved_signature_state: SignedSourceSignatureState | None = None

    def __post_init__(self) -> None:
        _require_uuid4(self.source_id, "preflight.inspection_source")
        _require_sha256(self.source_sha256, "preflight.inspection_digest")
        if (
            type(self.position) is not int
            or not 1 <= self.position <= MAX_PREFLIGHT_SOURCES
            or type(self.media_type) is not DetectedMediaType
            or type(self.availability) is not SourceInspectionAvailability
        ):
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.inspection")
        _require_saved_signature_state(self.media_type, self.saved_signature_state)
        if self.media_type is DetectedMediaType.PDF:
            if self.availability is SourceInspectionAvailability.CURRENT:
                if type(self.pdf_result) is not PdfInspectionResult:
                    reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.inspection_pdf")
            elif (
                self.availability is not SourceInspectionAvailability.UNAVAILABLE
                or self.pdf_result is not None
            ):
                reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.inspection_pdf")
        elif (
            self.availability is not SourceInspectionAvailability.NOT_APPLICABLE
            or self.pdf_result is not None
        ):
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.inspection_image")

    def __repr__(self) -> str:
        return (
            "SourceInspectionFact("
            f"position={self.position!r}, media_type={self.media_type.value!r}, "
            f"availability={self.availability.value!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class SourceInspectionsPreflightFacts(PreflightFactValue):
    """Complete ordered source inspection coverage for one saved case revision."""

    case_id: UUID
    manifest_revision: int
    inspections: tuple[SourceInspectionFact, ...] = field(repr=False)

    def __post_init__(self) -> None:
        _require_case_revision(self.case_id, self.manifest_revision, "preflight.inspections")
        if (
            type(self.inspections) is not tuple
            or len(self.inspections) > MAX_PREFLIGHT_SOURCES
            or any(type(item) is not SourceInspectionFact for item in self.inspections)
            or tuple(item.position for item in self.inspections)
            != tuple(range(1, len(self.inspections) + 1))
            or len({item.source_id for item in self.inspections}) != len(self.inspections)
        ):
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.inspection_facts")

    def __repr__(self) -> str:
        unavailable_count = sum(
            item.availability is SourceInspectionAvailability.UNAVAILABLE
            for item in self.inspections
        )
        return (
            "SourceInspectionsPreflightFacts("
            f"manifest_revision={self.manifest_revision!r}, "
            f"source_count={len(self.inspections)}, unavailable_count={unavailable_count})"
        )


class QualityFactAvailability(StrEnum):
    """Whether page-quality analysis is current for the saved page order."""

    CURRENT = "current"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True, repr=False)
class QualityPreflightFacts(PreflightFactValue):
    """Revision/profile-bound advisory quality findings without private fingerprints."""

    case_id: UUID
    manifest_revision: int
    availability: QualityFactAvailability
    page_ids: tuple[UUID, ...] = field(repr=False)
    findings: tuple[QualityFinding, ...] = field(repr=False)
    profile_id: str | None
    profile_sha256: str | None = field(repr=False)

    def __post_init__(self) -> None:
        _require_case_revision(self.case_id, self.manifest_revision, "preflight.quality")
        _require_uuid_inventory(
            self.page_ids,
            maximum=MAX_PREFLIGHT_PAGES,
            allow_empty=True,
            context="preflight.quality_pages",
        )
        if (
            type(self.availability) is not QualityFactAvailability
            or type(self.findings) is not tuple
            or len(self.findings) > MAX_QUALITY_FINDINGS_PER_PAGE * len(self.page_ids)
            or any(type(item) is not QualityFinding for item in self.findings)
            or self.findings != tuple(sorted(self.findings, key=quality_finding_sort_key))
            or len(set(self.findings)) != len(self.findings)
        ):
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.quality_facts")
        if self.availability is QualityFactAvailability.CURRENT:
            _require_sha256(self.profile_sha256, "preflight.quality_profile")
            if (
                type(self.profile_id) is not str
                or not 1 <= len(self.profile_id) <= 128
                or _PROFILE_TOKEN.fullmatch(self.profile_id) is None
            ):
                reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.quality_profile")
        elif self.profile_id is not None or self.profile_sha256 is not None or self.findings:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.quality_unavailable")
        position_by_page = {
            page_id: position for position, page_id in enumerate(self.page_ids, start=1)
        }
        finding_counts_by_page = dict.fromkeys(self.page_ids, 0)
        for finding in self.findings:
            if position_by_page.get(finding.page_id) != finding.page_position:
                reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.quality_binding")
            if (
                finding.related_page_id is not None
                and position_by_page.get(finding.related_page_id) != finding.related_page_position
            ):
                reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.quality_binding")
            finding_counts_by_page[finding.page_id] += 1
            if finding_counts_by_page[finding.page_id] > MAX_QUALITY_FINDINGS_PER_PAGE:
                reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.quality_capacity")

    def __repr__(self) -> str:
        return (
            "QualityPreflightFacts("
            f"manifest_revision={self.manifest_revision!r}, "
            f"availability={self.availability.value!r}, page_count={len(self.page_ids)}, "
            f"finding_count={len(self.findings)}, has_profile={self.profile_id is not None})"
        )


class OutputPlanAvailability(StrEnum):
    """Whether an exact current output plan is available for evaluation."""

    CURRENT = "current"
    UNAVAILABLE = "unavailable"


class OutputOverlayFlatteningAvailability(StrEnum):
    """Whether an exact output plan identifies every page whose overlays will flatten."""

    CURRENT = "current"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True, repr=False)
class OutputItemPreflightFact:
    """One deterministic plan target without a proposed filename or path."""

    output_key: str
    position: int
    page_ids: tuple[UUID, ...] = field(repr=False)
    predicted_bytes: int | None = None

    def __post_init__(self) -> None:
        _require_token(self.output_key, "preflight.output_key")
        _require_uuid_inventory(
            self.page_ids,
            maximum=MAX_PREFLIGHT_PAGES,
            allow_empty=False,
            context="preflight.output_pages",
        )
        if (
            type(self.position) is not int
            or not 1 <= self.position <= MAX_PREFLIGHT_OUTPUTS
            or (
                self.predicted_bytes is not None
                and (
                    type(self.predicted_bytes) is not int
                    or not 0 <= self.predicted_bytes <= MAX_PREFLIGHT_STATEMENT_VERSION
                )
            )
        ):
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.output_item")

    def __repr__(self) -> str:
        return (
            "OutputItemPreflightFact("
            f"output_key={self.output_key!r}, position={self.position!r}, "
            f"page_count={len(self.page_ids)}, has_predicted_bytes={self.predicted_bytes is not None})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class OutputPlanPreflightFacts(PreflightFactValue):
    """Current or explicitly unavailable internal output-plan projection."""

    case_id: UUID
    manifest_revision: int
    availability: OutputPlanAvailability
    output_mode: str
    items: tuple[OutputItemPreflightFact, ...] = field(repr=False)
    overlay_flattening_availability: OutputOverlayFlatteningAvailability = (
        OutputOverlayFlatteningAvailability.UNAVAILABLE
    )
    flattened_overlay_page_ids: tuple[UUID, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        _require_case_revision(self.case_id, self.manifest_revision, "preflight.output_plan")
        _require_token(self.output_mode, "preflight.output_mode")
        if (
            type(self.availability) is not OutputPlanAvailability
            or type(self.items) is not tuple
            or len(self.items) > MAX_PREFLIGHT_OUTPUTS
            or any(type(item) is not OutputItemPreflightFact for item in self.items)
            or type(self.overlay_flattening_availability) is not OutputOverlayFlatteningAvailability
        ):
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.output_plan")
        if self.availability is OutputPlanAvailability.CURRENT:
            page_ids = tuple(page_id for item in self.items for page_id in item.page_ids)
            if (
                not self.items
                or tuple(item.position for item in self.items)
                != tuple(range(1, len(self.items) + 1))
                or len({item.output_key for item in self.items}) != len(self.items)
                or len(page_ids) > MAX_PREFLIGHT_PAGES
                or len(set(page_ids)) != len(page_ids)
            ):
                reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.output_items")
            _require_uuid_inventory(
                self.flattened_overlay_page_ids,
                maximum=MAX_PREFLIGHT_PAGES,
                allow_empty=True,
                context="preflight.output_overlay_pages",
            )
            if self.overlay_flattening_availability is OutputOverlayFlatteningAvailability.CURRENT:
                flattened_page_ids = frozenset(self.flattened_overlay_page_ids)
                if self.flattened_overlay_page_ids != tuple(
                    page_id for page_id in page_ids if page_id in flattened_page_ids
                ):
                    reject_preflight(
                        PreflightErrorCode.MODEL_INVALID,
                        "preflight.output_overlay_pages",
                    )
            elif self.flattened_overlay_page_ids:
                reject_preflight(
                    PreflightErrorCode.MODEL_INVALID,
                    "preflight.output_overlay_pages",
                )
        elif self.items:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.output_items")
        elif (
            self.overlay_flattening_availability
            is not OutputOverlayFlatteningAvailability.UNAVAILABLE
            or self.flattened_overlay_page_ids
        ):
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.output_overlay_pages")

    def __repr__(self) -> str:
        return (
            "OutputPlanPreflightFacts("
            f"manifest_revision={self.manifest_revision!r}, "
            f"availability={self.availability.value!r}, output_count={len(self.items)}, "
            f"overlay_flattening_availability={self.overlay_flattening_availability.value!r})"
        )


def validate_canonical_preflight_bindings(
    inputs: Mapping[PreflightDependency, PreflightInput],
    authority: PreflightAuthority,
) -> None:
    """Fail closed when present canonical fact groups disagree with authority.

    The generic engine intentionally permits test and future rule-specific fact
    dataclasses. Whenever a dependency uses a canonical production DTO, this
    validator correlates its exact template, source, page, checklist, and output
    inventories before any rule runs.
    """

    if not isinstance(inputs, Mapping) or type(authority) is not PreflightAuthority:
        reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.bindings")
    if any(
        type(dependency) is not PreflightDependency or type(item) is not PreflightInput
        for dependency, item in inputs.items()
    ):
        reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.bindings")
    canonical_dependencies: dict[type[PreflightFactValue], PreflightDependency] = {
        CasePreflightFacts: PreflightDependency.CASE,
        TemplatePreflightFacts: PreflightDependency.TEMPLATE,
        ChecklistPreflightFacts: PreflightDependency.CHECKLIST,
        SourceInspectionsPreflightFacts: PreflightDependency.SOURCE_INSPECTIONS,
        QualityPreflightFacts: PreflightDependency.QUALITY_FINDINGS,
        OutputPlanPreflightFacts: PreflightDependency.OUTPUT_PLAN,
    }
    for dependency, item in inputs.items():
        expected_dependency = canonical_dependencies.get(type(item.value))
        if expected_dependency is not None and dependency is not expected_dependency:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.binding_dependency")
    case_item = inputs.get(PreflightDependency.CASE)
    template_item = inputs.get(PreflightDependency.TEMPLATE)
    checklist_item = inputs.get(PreflightDependency.CHECKLIST)
    inspections_item = inputs.get(PreflightDependency.SOURCE_INSPECTIONS)
    quality_item = inputs.get(PreflightDependency.QUALITY_FINDINGS)
    output_item = inputs.get(PreflightDependency.OUTPUT_PLAN)
    case_facts = (
        case_item.value
        if type(case_item) is PreflightInput and type(case_item.value) is CasePreflightFacts
        else None
    )
    template_facts = (
        template_item.value
        if type(template_item) is PreflightInput
        and type(template_item.value) is TemplatePreflightFacts
        else None
    )
    checklist_facts = (
        checklist_item.value
        if type(checklist_item) is PreflightInput
        and type(checklist_item.value) is ChecklistPreflightFacts
        else None
    )
    inspection_facts = (
        inspections_item.value
        if type(inspections_item) is PreflightInput
        and type(inspections_item.value) is SourceInspectionsPreflightFacts
        else None
    )
    quality_facts = (
        quality_item.value
        if type(quality_item) is PreflightInput
        and type(quality_item.value) is QualityPreflightFacts
        else None
    )
    output_facts = (
        output_item.value
        if type(output_item) is PreflightInput
        and type(output_item.value) is OutputPlanPreflightFacts
        else None
    )

    if case_facts is None and any(
        item is not None
        for item in (
            template_facts,
            checklist_facts,
            inspection_facts,
            quality_facts,
            output_facts,
        )
    ):
        reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.binding_case")

    if case_facts is not None:
        if case_facts.template_binding != authority.template_binding:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.binding_template")
        _require_catalog_scopes(
            authority,
            PreflightScopeKind.SOURCE,
            tuple(
                PreflightScope(PreflightScopeKind.SOURCE, item.source_id, item.position)
                for item in case_facts.sources
            ),
        )
        _require_catalog_scopes(
            authority,
            PreflightScopeKind.PAGE,
            tuple(
                PreflightScope(PreflightScopeKind.PAGE, page_id, position)
                for position, page_id in enumerate(case_facts.active_page_ids, start=1)
            ),
        )
    if template_facts is not None and template_facts.binding != authority.template_binding:
        reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.binding_template")
    if (
        checklist_facts is not None
        and checklist_facts.availability is ChecklistFactAvailability.CURRENT
    ):
        definition = checklist_facts.definition
        projection = checklist_facts.projection
        if (
            type(definition) is not ChecklistDefinition
            or type(projection) is not ChecklistProjection
        ):
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.binding_checklist")
        if definition.binding != authority.template_binding:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.binding_template")
        if case_facts is not None and projection.active_page_ids != case_facts.active_page_ids:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.binding_pages")
        _require_catalog_scopes(
            authority,
            PreflightScopeKind.REQUIREMENT,
            tuple(
                PreflightScope(
                    PreflightScopeKind.REQUIREMENT,
                    requirement.requirement_id,
                    position,
                )
                for position, requirement in enumerate(definition.requirements, start=1)
            ),
        )
        assignments = tuple(
            assignment
            for requirement_projection in projection.requirements
            for assignment in requirement_projection.assignments
        )
        _require_catalog_scopes(
            authority,
            PreflightScopeKind.ASSIGNMENT,
            tuple(
                PreflightScope(PreflightScopeKind.ASSIGNMENT, assignment.assignment_id, position)
                for position, assignment in enumerate(assignments, start=1)
            ),
        )
    if case_facts is not None and inspection_facts is not None:
        case_sources = tuple(
            (
                item.source_id,
                item.position,
                item.media_type,
                item.source_sha256,
                item.saved_signature_state,
            )
            for item in case_facts.sources
        )
        inspections = tuple(
            (
                item.source_id,
                item.position,
                item.media_type,
                item.source_sha256,
                item.saved_signature_state,
            )
            for item in inspection_facts.inspections
        )
        if case_sources != inspections:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.binding_sources")
    if case_facts is not None and quality_facts is not None:
        if quality_facts.page_ids != case_facts.active_page_ids:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.binding_pages")
    if case_facts is not None and output_facts is not None:
        active_pages = frozenset(case_facts.active_page_ids)
        if any(not set(item.page_ids).issubset(active_pages) for item in output_facts.items):
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.binding_outputs")
        expected_outputs = tuple(
            PreflightScope(PreflightScopeKind.OUTPUT, item.output_key, item.position)
            for item in output_facts.items
        )
        _require_catalog_scopes(authority, PreflightScopeKind.OUTPUT, expected_outputs)
    if (
        template_facts is not None
        and checklist_facts is not None
        and checklist_facts.availability is ChecklistFactAvailability.CURRENT
    ):
        definition = checklist_facts.definition
        if type(definition) is not ChecklistDefinition:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.binding_checklist")
        requirement_ids = {item.requirement_id for item in definition.requirements}
        if any(
            notice.requirement_id is not None and notice.requirement_id not in requirement_ids
            for notice in template_facts.notices
        ):
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.binding_notices")


def _require_saved_signature_state(
    media_type: DetectedMediaType,
    state: SignedSourceSignatureState | None,
) -> None:
    if state is None:
        return
    if (
        type(state) is not SignedSourceSignatureState
        or (
            media_type is DetectedMediaType.PDF
            and state is SignedSourceSignatureState.NOT_APPLICABLE
        )
        or (
            media_type is not DetectedMediaType.PDF
            and state is not SignedSourceSignatureState.NOT_APPLICABLE
        )
    ):
        reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.saved_signature_state")


def _require_catalog_scopes(
    authority: PreflightAuthority,
    kind: PreflightScopeKind,
    expected: tuple[PreflightScope, ...],
) -> None:
    actual = tuple(scope for scope in authority.scope_catalog.scopes if scope.kind is kind)
    if actual != expected:
        reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.binding_scopes")


def _require_case_revision(case_id: object, revision: object, context: str) -> None:
    _require_uuid4(case_id, context)
    if type(revision) is not int or not 0 <= revision <= MAX_PREFLIGHT_STATEMENT_VERSION:
        reject_preflight(PreflightErrorCode.MODEL_INVALID, context)


def _require_uuid_inventory(
    value: object,
    *,
    maximum: int,
    allow_empty: bool,
    context: str,
) -> tuple[UUID, ...]:
    if (
        type(value) is not tuple
        or (not allow_empty and not value)
        or len(value) > maximum
        or any(type(item) is not UUID or item.version != 4 for item in value)
        or len(set(value)) != len(value)
    ):
        reject_preflight(PreflightErrorCode.MODEL_INVALID, context)
    return value


def _require_token(value: object, context: str) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= min(128, MAX_PREFLIGHT_FACT_STRING_CODEPOINTS)
        or _TOKEN.fullmatch(value) is None
    ):
        reject_preflight(PreflightErrorCode.MODEL_INVALID, context)
    return value


__all__ = (
    "CasePageDetailsAvailability",
    "CasePagePreflightFact",
    "CaseSourcePreflightFact",
    "CasePreflightFacts",
    "ChecklistFactAvailability",
    "ChecklistPreflightFacts",
    "OutputItemPreflightFact",
    "OutputOverlayFlatteningAvailability",
    "OutputPlanAvailability",
    "OutputPlanPreflightFacts",
    "QualityFactAvailability",
    "QualityPreflightFacts",
    "SourceInspectionAvailability",
    "SourceInspectionFact",
    "SourceInspectionsPreflightFacts",
    "TemplateFactAvailability",
    "TemplateNoticeFact",
    "TemplateNoticeKind",
    "TemplatePreflightFacts",
    "validate_canonical_preflight_bindings",
)

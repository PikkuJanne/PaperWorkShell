"""Pure, authority-bound package planning for template checklist exports.

The planner consumes only immutable saved state and validated template-domain
objects.  It performs no filesystem access and deliberately keeps filenames,
case identifiers, page identifiers, assignment identifiers, and digests out of
representations and errors.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Never, cast
from uuid import UUID

from paperworkshell.domain.checklist import (
    AssignmentReviewState,
    ChecklistCommandHistory,
    ChecklistDefinition,
    ChecklistError,
    ChecklistErrorCode,
    RequirementCountUnit,
    TemplateBinding,
)
from paperworkshell.domain.manifest_repository import (
    CURRENT_MANIFEST_SCHEMA_VERSION,
    MAX_SAFE_MANIFEST_INTEGER,
    ManifestSnapshot,
)
from paperworkshell.preflight.facts import (
    OutputItemPreflightFact,
    OutputOverlayFlatteningAvailability,
    OutputPlanAvailability,
    OutputPlanPreflightFacts,
)
from paperworkshell.preflight.models import PreflightError
from paperworkshell.preflight.portal_constraints import (
    MAX_PORTAL_OUTPUTS,
    PORTAL_CONSTRAINTS_VERSION,
    PortalConstraintProfile,
    PortalConstraints,
    PortalEncryptionPolicy,
    PortalFilenameRequest,
    PortalOutputMode,
    ResolvedOutputFilename,
    normalize_output_filename,
    resolve_output_filenames,
    resolve_zip_archive_filename,
)

PACKAGE_EXPORT_PLAN_VERSION: Final = 1
PACKAGE_EXPORT_SEMANTIC_FINGERPRINT_VERSION: Final = 1

_CONTEXT: Final = re.compile(r"[a-z][a-z0-9_.]{0,79}\Z")
_TOKEN: Final = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*\Z")
_SHA256: Final = re.compile(r"[0-9a-f]{64}\Z")
_CAS_TOKEN: Final = re.compile(r"[A-Za-z0-9_-]{16,128}\Z")
_TEMPLATE_REF_MEMBERS: Final = frozenset({"template_id", "template_version", "sha256"})
_CONSTRAINT_REQUIRED_MEMBERS: Final = frozenset(
    {"constraints_version", "allowed_output_modes", "encryption_policy"}
)
_CONSTRAINT_OPTIONAL_MEMBERS: Final = frozenset(
    {
        "maximum_file_count",
        "maximum_total_bytes",
        "maximum_file_bytes",
        "maximum_pages_per_file",
        "requested_output_names",
    }
)


class PackagePlannerErrorCode(StrEnum):
    """Closed registry of content-free package-planning failures."""

    MODEL_INVALID = "PACKAGE_PLANNER_MODEL_INVALID"
    MANIFEST_INVALID = "PACKAGE_PLANNER_MANIFEST_INVALID"
    TEMPLATE_MISMATCH = "PACKAGE_PLANNER_TEMPLATE_MISMATCH"
    MODE_NOT_ALLOWED = "PACKAGE_PLANNER_MODE_NOT_ALLOWED"
    REQUIREMENT_MISSING = "PACKAGE_PLANNER_REQUIREMENT_MISSING"
    REQUIREMENT_EXCESSIVE = "PACKAGE_PLANNER_REQUIREMENT_EXCESSIVE"
    ASSIGNMENT_UNCONFIRMED = "PACKAGE_PLANNER_ASSIGNMENT_UNCONFIRMED"
    PAGE_INACTIVE = "PACKAGE_PLANNER_PAGE_INACTIVE"
    PAGE_UNASSIGNED = "PACKAGE_PLANNER_PAGE_UNASSIGNED"
    PAGE_DUPLICATE = "PACKAGE_PLANNER_PAGE_DUPLICATE"
    PLAN_EMPTY = "PACKAGE_PLANNER_PLAN_EMPTY"
    FILENAME_COUNT_INVALID = "PACKAGE_PLANNER_FILENAME_COUNT_INVALID"
    FILENAME_EXTENSION_INVALID = "PACKAGE_PLANNER_FILENAME_EXTENSION_INVALID"
    FILENAME_COLLISION = "PACKAGE_PLANNER_FILENAME_COLLISION"
    FILENAME_INVALID = "PACKAGE_PLANNER_FILENAME_INVALID"
    PATH_LENGTH_EXCEEDED = "PACKAGE_PLANNER_PATH_LENGTH_EXCEEDED"
    LIMIT_EXCEEDED = "PACKAGE_PLANNER_LIMIT_EXCEEDED"


_SAFE_MESSAGES: Final[dict[PackagePlannerErrorCode, str]] = {
    PackagePlannerErrorCode.MODEL_INVALID: "PaperWorkShell received invalid package-plan data.",
    PackagePlannerErrorCode.MANIFEST_INVALID: (
        "The saved case record cannot be used to plan this package."
    ),
    PackagePlannerErrorCode.TEMPLATE_MISMATCH: (
        "The saved case and selected checklist template do not match."
    ),
    PackagePlannerErrorCode.MODE_NOT_ALLOWED: (
        "Choose an output organization allowed by the saved case constraints."
    ),
    PackagePlannerErrorCode.REQUIREMENT_MISSING: (
        "Add and confirm the required checklist documents before export."
    ),
    PackagePlannerErrorCode.REQUIREMENT_EXCESSIVE: (
        "Reduce the confirmed checklist items to the supported maximum before export."
    ),
    PackagePlannerErrorCode.ASSIGNMENT_UNCONFIRMED: (
        "Review and confirm every document assignment before export."
    ),
    PackagePlannerErrorCode.PAGE_INACTIVE: (
        "Restore or remove checklist assignments that refer to removed pages."
    ),
    PackagePlannerErrorCode.PAGE_UNASSIGNED: (
        "Assign every active page to a checklist category before export."
    ),
    PackagePlannerErrorCode.PAGE_DUPLICATE: (
        "Place each active page in exactly one confirmed document assignment."
    ),
    PackagePlannerErrorCode.PLAN_EMPTY: "Add at least one assigned active page before export.",
    PackagePlannerErrorCode.FILENAME_COUNT_INVALID: (
        "Provide exactly the number of output names required by the selected organization."
    ),
    PackagePlannerErrorCode.FILENAME_EXTENSION_INVALID: (
        "Use the file extension required by the selected output organization."
    ),
    PackagePlannerErrorCode.FILENAME_COLLISION: (
        "Choose output names that remain distinct on Windows."
    ),
    PackagePlannerErrorCode.FILENAME_INVALID: "Choose safe single-component output names.",
    PackagePlannerErrorCode.PATH_LENGTH_EXCEEDED: (
        "Choose a shorter output name or a destination with a shorter path."
    ),
    PackagePlannerErrorCode.LIMIT_EXCEEDED: (
        "The package is larger than the supported planning capacity."
    ),
}


@dataclass(slots=True, repr=False)
class PackagePlannerError(ValueError):
    """Stable failure that retains no names, identifiers, paths, or digests."""

    code: PackagePlannerErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not PackagePlannerErrorCode:
            self.code = PackagePlannerErrorCode.MODEL_INVALID
        if type(self.context) is not str or _CONTEXT.fullmatch(self.context) is None:
            self.context = "package.planner"
        self.safe_message = _SAFE_MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.safe_message)

    def __repr__(self) -> str:
        return f"PackagePlannerError(code={self.code.value!r}, context={self.context!r})"

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject_package_planner(code: PackagePlannerErrorCode, context: str) -> Never:
    """Raise one sanitized package-planning failure."""

    raise PackagePlannerError(code, context) from None


@dataclass(frozen=True, slots=True, repr=False)
class PackagePlanAuthority:
    """Exact saved case and template identity used to derive a package plan."""

    case_id: UUID = field(repr=False)
    manifest_revision: int
    manifest_sha256: str = field(repr=False)
    manifest_cas_token: str = field(repr=False)
    template_binding: TemplateBinding = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not _is_uuid4(self.case_id)
            or type(self.manifest_revision) is not int
            or not 0 <= self.manifest_revision <= MAX_SAFE_MANIFEST_INTEGER
            or type(self.manifest_sha256) is not str
            or _SHA256.fullmatch(self.manifest_sha256) is None
            or type(self.manifest_cas_token) is not str
            or _CAS_TOKEN.fullmatch(self.manifest_cas_token) is None
            or type(self.template_binding) is not TemplateBinding
        ):
            reject_package_planner(PackagePlannerErrorCode.MODEL_INVALID, "package.authority")

    def __repr__(self) -> str:
        return f"PackagePlanAuthority(manifest_revision={self.manifest_revision!r})"


@dataclass(frozen=True, slots=True, repr=False)
class ActivePackageConstraints:
    """The exact persisted constraint subset active for this plan."""

    values: PortalConstraints
    requested_output_names: tuple[str, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.values) is not PortalConstraints
            or self.values.default_mode is not None
            or type(self.requested_output_names) is not tuple
            or len(self.requested_output_names) > MAX_PORTAL_OUTPUTS
            or any(type(value) is not str for value in self.requested_output_names)
        ):
            reject_package_planner(PackagePlannerErrorCode.MODEL_INVALID, "package.constraints")
        normalized: list[str] = []
        try:
            for value in self.requested_output_names:
                selected = normalize_output_filename(value)
                if selected != value:
                    raise ValueError
                normalized.append(selected)
        except PreflightError, ValueError:
            reject_package_planner(
                PackagePlannerErrorCode.MODEL_INVALID, "package.constraint_names"
            )
        if len({value.casefold() for value in normalized}) != len(normalized):
            reject_package_planner(
                PackagePlannerErrorCode.MODEL_INVALID, "package.constraint_names"
            )

    def __repr__(self) -> str:
        return (
            "ActivePackageConstraints("
            f"allowed_mode_count={len(self.values.allowed_modes)}, "
            f"requested_name_count={len(self.requested_output_names)})"
        )

    @property
    def constraints(self) -> PortalConstraints:
        """Return the validated persisted constraint values."""

        return self.values


@dataclass(frozen=True, slots=True, repr=False)
class PlannedPackagePdf:
    """One ordered PDF output or ZIP member and its private checklist authority."""

    output_key: str
    position: int
    requirement_id: str | None
    assignment_ids: tuple[UUID, ...] = field(repr=False)
    page_ids: tuple[UUID, ...] = field(repr=False)
    filename: str = field(repr=False)
    collision_index: int

    def __post_init__(self) -> None:
        if (
            not _is_token(self.output_key)
            or type(self.position) is not int
            or not 1 <= self.position <= MAX_PORTAL_OUTPUTS
            or (self.requirement_id is not None and not _is_token(self.requirement_id))
            or type(self.assignment_ids) is not tuple
            or not self.assignment_ids
            or any(not _is_uuid4(value) for value in self.assignment_ids)
            or len(set(self.assignment_ids)) != len(self.assignment_ids)
            or type(self.page_ids) is not tuple
            or not self.page_ids
            or any(not _is_uuid4(value) for value in self.page_ids)
            or len(set(self.page_ids)) != len(self.page_ids)
            or type(self.collision_index) is not int
            or not 1 <= self.collision_index <= MAX_PORTAL_OUTPUTS + 1
        ):
            reject_package_planner(PackagePlannerErrorCode.MODEL_INVALID, "package.pdf")
        try:
            normalized = normalize_output_filename(self.filename)
        except PreflightError:
            reject_package_planner(PackagePlannerErrorCode.MODEL_INVALID, "package.pdf_name")
        if normalized != self.filename or not self.filename.casefold().endswith(".pdf"):
            reject_package_planner(PackagePlannerErrorCode.MODEL_INVALID, "package.pdf_name")

    def __repr__(self) -> str:
        return (
            "PlannedPackagePdf("
            f"position={self.position!r}, assignment_count={len(self.assignment_ids)}, "
            f"page_count={len(self.page_ids)}, "
            f"has_requirement={self.requirement_id is not None}, "
            f"collision_index={self.collision_index!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class PlannedPackageArchive:
    """One outer ZIP component with ordered member-key authority."""

    output_key: str
    filename: str = field(repr=False)
    collision_index: int
    member_output_keys: tuple[str, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not _is_token(self.output_key)
            or type(self.collision_index) is not int
            or self.collision_index != 1
            or type(self.member_output_keys) is not tuple
            or not self.member_output_keys
            or any(not _is_token(value) for value in self.member_output_keys)
            or len(set(self.member_output_keys)) != len(self.member_output_keys)
        ):
            reject_package_planner(PackagePlannerErrorCode.MODEL_INVALID, "package.archive")
        try:
            normalized = normalize_output_filename(self.filename)
        except PreflightError:
            reject_package_planner(PackagePlannerErrorCode.MODEL_INVALID, "package.archive_name")
        if normalized != self.filename or not self.filename.casefold().endswith(".zip"):
            reject_package_planner(PackagePlannerErrorCode.MODEL_INVALID, "package.archive_name")

    def __repr__(self) -> str:
        return f"PlannedPackageArchive(member_count={len(self.member_output_keys)})"


@dataclass(frozen=True, slots=True, init=False, repr=False)
class PackageExportPlan:
    """Immutable grouping, naming, facts, and exact saved authority for one package."""

    authority: PackagePlanAuthority
    mode: PortalOutputMode
    active_constraints: ActivePackageConstraints
    pdfs: tuple[PlannedPackagePdf, ...]
    archive: PlannedPackageArchive | None
    output_facts: OutputPlanPreflightFacts
    semantic_fingerprint: str = field(repr=False)
    plan_version: int

    def __init__(
        self,
        *,
        authority: PackagePlanAuthority,
        mode: PortalOutputMode,
        active_constraints: ActivePackageConstraints,
        pdfs: tuple[PlannedPackagePdf, ...],
        archive: PlannedPackageArchive | None,
        output_facts: OutputPlanPreflightFacts,
        plan_version: int = PACKAGE_EXPORT_PLAN_VERSION,
    ) -> None:
        if (
            type(authority) is not PackagePlanAuthority
            or type(mode) is not PortalOutputMode
            or type(active_constraints) is not ActivePackageConstraints
            or type(pdfs) is not tuple
            or not 1 <= len(pdfs) <= MAX_PORTAL_OUTPUTS
            or any(type(value) is not PlannedPackagePdf for value in pdfs)
            or (archive is not None and type(archive) is not PlannedPackageArchive)
            or type(output_facts) is not OutputPlanPreflightFacts
            or type(plan_version) is not int
            or plan_version != PACKAGE_EXPORT_PLAN_VERSION
        ):
            reject_package_planner(PackagePlannerErrorCode.MODEL_INVALID, "package.plan")
        output_keys = tuple(value.output_key for value in pdfs)
        page_ids = tuple(page_id for value in pdfs for page_id in value.page_ids)
        assignment_ids = tuple(
            assignment_id for value in pdfs for assignment_id in value.assignment_ids
        )
        requirement_ids = tuple(
            value.requirement_id for value in pdfs if value.requirement_id is not None
        )
        facts_match = (
            output_facts.case_id == authority.case_id
            and output_facts.manifest_revision == authority.manifest_revision
            and output_facts.availability is OutputPlanAvailability.CURRENT
            and output_facts.output_mode == mode.value
            and len(output_facts.items) == len(pdfs)
            and all(
                item.output_key == pdf.output_key
                and item.position == pdf.position
                and item.page_ids == pdf.page_ids
                and item.predicted_bytes is None
                for item, pdf in zip(output_facts.items, pdfs, strict=True)
            )
        )
        if (
            mode not in active_constraints.values.allowed_modes
            or tuple(value.position for value in pdfs) != tuple(range(1, len(pdfs) + 1))
            or len(set(output_keys)) != len(output_keys)
            or len(set(page_ids)) != len(page_ids)
            or len(set(assignment_ids)) != len(assignment_ids)
            or len(set(requirement_ids)) != len(requirement_ids)
            or not facts_match
        ):
            reject_package_planner(PackagePlannerErrorCode.MODEL_INVALID, "package.plan_bindings")
        if mode is PortalOutputMode.COMBINED_PDF:
            valid_shape = len(pdfs) == 1 and pdfs[0].requirement_id is None and archive is None
        elif mode is PortalOutputMode.SEPARATE_PDFS:
            valid_shape = (
                all(value.requirement_id is not None for value in pdfs) and archive is None
            )
        else:
            valid_shape = (
                all(value.requirement_id is not None for value in pdfs)
                and archive is not None
                and archive.output_key not in set(output_keys)
                and archive.member_output_keys == output_keys
            )
        if not valid_shape:
            reject_package_planner(PackagePlannerErrorCode.MODEL_INVALID, "package.plan_shape")
        fingerprint = _semantic_fingerprint(
            authority.template_binding,
            mode,
            active_constraints,
            pdfs,
            archive,
            plan_version,
        )
        object.__setattr__(self, "authority", authority)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "active_constraints", active_constraints)
        object.__setattr__(self, "pdfs", pdfs)
        object.__setattr__(self, "archive", archive)
        object.__setattr__(self, "output_facts", output_facts)
        object.__setattr__(self, "semantic_fingerprint", fingerprint)
        object.__setattr__(self, "plan_version", plan_version)

    @property
    def planned_pdfs(self) -> tuple[PlannedPackagePdf, ...]:
        """Return the ordered PDF outputs (or ordered ZIP members)."""

        return self.pdfs

    @property
    def planned_archive(self) -> PlannedPackageArchive | None:
        """Return the outer ZIP plan when ZIP organization is selected."""

        return self.archive

    def __repr__(self) -> str:
        return (
            "PackageExportPlan("
            f"mode={self.mode.value!r}, output_count={len(self.pdfs)}, "
            f"has_archive={self.archive is not None}, plan_version={self.plan_version})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class _RequirementGroup:
    requirement_id: str
    assignment_ids: tuple[UUID, ...] = field(repr=False)
    page_ids: tuple[UUID, ...] = field(repr=False)


def build_package_export_plan(
    snapshot: ManifestSnapshot,
    definition: ChecklistDefinition,
    profile: PortalConstraintProfile,
    mode: PortalOutputMode,
    *,
    destination_parent_utf16_units: int = 0,
) -> PackageExportPlan:
    """Build the sole deterministic plan for one exact saved manifest revision."""

    if (
        type(snapshot) is not ManifestSnapshot
        or type(definition) is not ChecklistDefinition
        or type(profile) is not PortalConstraintProfile
        or type(mode) is not PortalOutputMode
    ):
        reject_package_planner(PackagePlannerErrorCode.MODEL_INVALID, "package.inputs")
    if snapshot.schema_version != CURRENT_MANIFEST_SCHEMA_VERSION:
        reject_package_planner(PackagePlannerErrorCode.MANIFEST_INVALID, "package.manifest_version")
    if definition.binding != profile.binding:
        reject_package_planner(
            PackagePlannerErrorCode.TEMPLATE_MISMATCH, "package.template_profile"
        )
    try:
        document = snapshot.document
    except Exception:
        reject_package_planner(PackagePlannerErrorCode.MANIFEST_INVALID, "package.manifest")
    _require_manifest_binding(document.get("template_ref"), definition.binding)
    active_constraints = _active_constraints_from_manifest(document.get("constraints"))
    if mode not in active_constraints.values.allowed_modes:
        reject_package_planner(PackagePlannerErrorCode.MODE_NOT_ALLOWED, "package.mode")
    try:
        history = ChecklistCommandHistory.from_snapshot(snapshot, definition)
    except ChecklistError as error:
        code = (
            PackagePlannerErrorCode.TEMPLATE_MISMATCH
            if error.code
            in {ChecklistErrorCode.TEMPLATE_REQUIRED, ChecklistErrorCode.TEMPLATE_MISMATCH}
            else PackagePlannerErrorCode.MANIFEST_INVALID
        )
        reject_package_planner(code, "package.checklist")
    groups = _require_complete_partition(history)
    if not groups:
        reject_package_planner(PackagePlannerErrorCode.PLAN_EMPTY, "package.groups")
    resolved_pdfs, resolved_archive = _resolve_plan_names(
        groups,
        definition,
        profile,
        mode,
        active_constraints.requested_output_names,
        destination_parent_utf16_units,
    )
    pdfs = _planned_pdfs(groups, mode, resolved_pdfs)
    archive = (
        None
        if resolved_archive is None
        else PlannedPackageArchive(
            output_key=resolved_archive.output_key,
            filename=resolved_archive.filename,
            collision_index=resolved_archive.collision_index,
            member_output_keys=tuple(value.output_key for value in pdfs),
        )
    )
    output_facts = _output_facts(snapshot, document, mode, pdfs)
    authority = PackagePlanAuthority(
        case_id=snapshot.case_id,
        manifest_revision=snapshot.revision,
        manifest_sha256=snapshot.sha256,
        manifest_cas_token=snapshot.cas_token,
        template_binding=definition.binding,
    )
    return PackageExportPlan(
        authority=authority,
        mode=mode,
        active_constraints=active_constraints,
        pdfs=pdfs,
        archive=archive,
        output_facts=output_facts,
    )


def _require_manifest_binding(value: object, expected: TemplateBinding) -> None:
    if type(value) is not dict or frozenset(value) != _TEMPLATE_REF_MEMBERS:
        reject_package_planner(PackagePlannerErrorCode.TEMPLATE_MISMATCH, "package.template_ref")
    raw = cast(dict[str, object], value)
    try:
        binding = TemplateBinding(
            cast(str, raw["template_id"]),
            cast(str, raw["template_version"]),
            cast(str, raw["sha256"]),
        )
    except ChecklistError, TypeError:
        reject_package_planner(PackagePlannerErrorCode.MANIFEST_INVALID, "package.template_ref")
    if binding != expected:
        reject_package_planner(PackagePlannerErrorCode.TEMPLATE_MISMATCH, "package.template_ref")


def _active_constraints_from_manifest(value: object) -> ActivePackageConstraints:
    if type(value) is not dict:
        reject_package_planner(PackagePlannerErrorCode.MANIFEST_INVALID, "package.constraints")
    raw = cast(dict[str, object], value)
    members = frozenset(raw)
    if (
        not _CONSTRAINT_REQUIRED_MEMBERS <= members
        or not members <= _CONSTRAINT_REQUIRED_MEMBERS | _CONSTRAINT_OPTIONAL_MEMBERS
        or type(raw.get("constraints_version")) is not int
        or raw.get("constraints_version") != PORTAL_CONSTRAINTS_VERSION
    ):
        reject_package_planner(PackagePlannerErrorCode.MANIFEST_INVALID, "package.constraints")
    raw_modes = raw.get("allowed_output_modes")
    raw_names = raw.get("requested_output_names", [])
    if (
        type(raw_modes) is not list
        or type(raw_names) is not list
        or any(type(value) is not str for value in raw_names)
    ):
        reject_package_planner(PackagePlannerErrorCode.MANIFEST_INVALID, "package.constraints")
    try:
        modes = tuple(PortalOutputMode(value) for value in raw_modes)
        encryption_raw = raw["encryption_policy"]
        if type(encryption_raw) is not str:
            raise TypeError
        constraints = PortalConstraints(
            allowed_modes=modes,
            encryption_policy=PortalEncryptionPolicy(encryption_raw),
            maximum_file_count=_optional_positive_int(raw, "maximum_file_count"),
            maximum_total_bytes=_optional_positive_int(raw, "maximum_total_bytes"),
            maximum_file_bytes=_optional_positive_int(raw, "maximum_file_bytes"),
            maximum_pages_per_file=_optional_positive_int(raw, "maximum_pages_per_file"),
            default_mode=None,
        )
    except KeyError, TypeError, ValueError, PreflightError:
        reject_package_planner(PackagePlannerErrorCode.MANIFEST_INVALID, "package.constraints")
    names = tuple(cast(list[str], raw_names))
    normalized_names: list[str] = []
    try:
        for name in names:
            normalized = normalize_output_filename(name)
            if normalized != name:
                raise ValueError
            normalized_names.append(normalized)
    except PreflightError, ValueError:
        reject_package_planner(PackagePlannerErrorCode.FILENAME_INVALID, "package.requested_names")
    comparison = tuple(unicodedata.normalize("NFC", name).casefold() for name in normalized_names)
    if len(set(comparison)) != len(comparison):
        reject_package_planner(
            PackagePlannerErrorCode.FILENAME_COLLISION,
            "package.requested_names",
        )
    return ActivePackageConstraints(constraints, names)


def _optional_positive_int(values: dict[str, object], key: str) -> int | None:
    if key not in values:
        return None
    value = values[key]
    if type(value) is not int:
        raise TypeError
    return value


def _require_complete_partition(
    history: ChecklistCommandHistory,
) -> tuple[_RequirementGroup, ...]:
    projection = history.projection
    active = set(projection.active_page_ids)
    groups: list[_RequirementGroup] = []
    planned_pages: list[UUID] = []
    planned_assignment_ids: list[UUID] = []
    for item in projection.requirements:
        assignments = item.assignments
        if any(
            assignment.review_state is not AssignmentReviewState.USER_CONFIRMED
            for assignment in assignments
        ):
            reject_package_planner(
                PackagePlannerErrorCode.ASSIGNMENT_UNCONFIRMED,
                "package.assignment_state",
            )
        if any(
            page_id not in active for assignment in assignments for page_id in assignment.page_ids
        ):
            reject_package_planner(
                PackagePlannerErrorCode.PAGE_INACTIVE, "package.assignment_pages"
            )
        assignment_ids = tuple(assignment.assignment_id for assignment in assignments)
        page_ids = tuple(page_id for assignment in assignments for page_id in assignment.page_ids)
        if len(set(page_ids)) != len(page_ids):
            reject_package_planner(
                PackagePlannerErrorCode.PAGE_DUPLICATE, "package.requirement_pages"
            )
        count = (
            len(assignments)
            if item.requirement.count_unit is RequirementCountUnit.DOCUMENTS
            else len(page_ids)
        )
        if count < item.requirement.minimum_count:
            reject_package_planner(
                PackagePlannerErrorCode.REQUIREMENT_MISSING,
                "package.requirement_minimum",
            )
        if count > item.requirement.maximum_count:
            reject_package_planner(
                PackagePlannerErrorCode.REQUIREMENT_EXCESSIVE,
                "package.requirement_maximum",
            )
        if page_ids:
            groups.append(
                _RequirementGroup(item.requirement.requirement_id, assignment_ids, page_ids)
            )
            planned_pages.extend(page_ids)
            planned_assignment_ids.extend(assignment_ids)
    if len(set(planned_assignment_ids)) != len(planned_assignment_ids):
        reject_package_planner(PackagePlannerErrorCode.MANIFEST_INVALID, "package.assignment_ids")
    if len(set(planned_pages)) != len(planned_pages):
        reject_package_planner(PackagePlannerErrorCode.PAGE_DUPLICATE, "package.pages")
    if set(planned_pages) != active or len(planned_pages) != len(projection.active_page_ids):
        reject_package_planner(PackagePlannerErrorCode.PAGE_UNASSIGNED, "package.page_partition")
    if len(groups) > MAX_PORTAL_OUTPUTS:
        reject_package_planner(PackagePlannerErrorCode.LIMIT_EXCEEDED, "package.outputs")
    return tuple(groups)


def _resolve_plan_names(
    groups: tuple[_RequirementGroup, ...],
    definition: ChecklistDefinition,
    profile: PortalConstraintProfile,
    mode: PortalOutputMode,
    requested_names: tuple[str, ...],
    destination_parent_utf16_units: int,
) -> tuple[tuple[ResolvedOutputFilename, ...], ResolvedOutputFilename | None]:
    requests: tuple[PortalFilenameRequest, ...]
    try:
        if mode is PortalOutputMode.COMBINED_PDF:
            if requested_names:
                _require_requested_count(requested_names, 1)
                requests = (_requested_pdf("package", 1, requested_names[0]),)
            else:
                requests = (
                    PortalFilenameRequest("package", 1, profile.filename_policy.package_stem),
                )
            return (
                resolve_output_filenames(
                    requests,
                    mode,
                    destination_parent_utf16_units=destination_parent_utf16_units,
                ),
                None,
            )
        requirement_stems = {
            item.requirement_id: item.stem for item in profile.filename_policy.requirement_stems
        }
        definition_ids = {item.requirement_id for item in groups}
        profile_ids = set(requirement_stems)
        all_definition_ids = {item.requirement_id for item in definition.requirements}
        if profile_ids != all_definition_ids or not definition_ids <= profile_ids:
            reject_package_planner(
                PackagePlannerErrorCode.TEMPLATE_MISMATCH,
                "package.filename_policy",
            )
        if mode is PortalOutputMode.SEPARATE_PDFS and requested_names:
            _require_requested_count(requested_names, len(groups))
            requests = tuple(
                _requested_pdf(group.requirement_id, position, requested_names[position - 1])
                for position, group in enumerate(groups, start=1)
            )
        else:
            requests = tuple(
                PortalFilenameRequest(
                    group.requirement_id,
                    position,
                    requirement_stems[group.requirement_id],
                )
                for position, group in enumerate(groups, start=1)
            )
        pdf_parent_units = (
            destination_parent_utf16_units if mode is PortalOutputMode.SEPARATE_PDFS else 0
        )
        resolved_pdfs = resolve_output_filenames(
            requests,
            mode,
            destination_parent_utf16_units=pdf_parent_units,
        )
        if mode is PortalOutputMode.SEPARATE_PDFS:
            return resolved_pdfs, None
        if requested_names:
            _require_requested_count(requested_names, 1)
            archive_request = _requested_archive(requested_names[0])
        else:
            archive_request = PortalFilenameRequest(
                "archive",
                1,
                profile.filename_policy.package_stem,
            )
        archive = resolve_zip_archive_filename(
            archive_request,
            destination_parent_utf16_units=destination_parent_utf16_units,
        )
        return resolved_pdfs, archive
    except PackagePlannerError:
        raise
    except PreflightError as error:
        code = (
            PackagePlannerErrorCode.PATH_LENGTH_EXCEEDED
            if error.context
            in {"portal.parent_path_length", "portal.path_length", "portal.filename_length"}
            else PackagePlannerErrorCode.FILENAME_INVALID
        )
        reject_package_planner(code, "package.output_names")


def _require_requested_count(names: tuple[str, ...], expected: int) -> None:
    if len(names) != expected:
        reject_package_planner(
            PackagePlannerErrorCode.FILENAME_COUNT_INVALID,
            "package.requested_name_count",
        )


def _requested_pdf(output_key: str, position: int, filename: str) -> PortalFilenameRequest:
    if not filename.endswith(".pdf"):
        reject_package_planner(
            PackagePlannerErrorCode.FILENAME_EXTENSION_INVALID,
            "package.requested_extension",
        )
    request = PortalFilenameRequest(output_key, position, filename[:-4])
    resolved = resolve_output_filenames(
        (PortalFilenameRequest("probe", 1, request.stem),),
        PortalOutputMode.COMBINED_PDF,
    )[0]
    if resolved.filename != filename:
        reject_package_planner(PackagePlannerErrorCode.FILENAME_INVALID, "package.requested_name")
    return request


def _requested_archive(filename: str) -> PortalFilenameRequest:
    if not filename.endswith(".zip"):
        reject_package_planner(
            PackagePlannerErrorCode.FILENAME_EXTENSION_INVALID,
            "package.requested_extension",
        )
    request = PortalFilenameRequest("archive", 1, filename[:-4])
    resolved = resolve_zip_archive_filename(request)
    if resolved.filename != filename:
        reject_package_planner(PackagePlannerErrorCode.FILENAME_INVALID, "package.requested_name")
    return request


def _planned_pdfs(
    groups: tuple[_RequirementGroup, ...],
    mode: PortalOutputMode,
    names: tuple[ResolvedOutputFilename, ...],
) -> tuple[PlannedPackagePdf, ...]:
    if mode is PortalOutputMode.COMBINED_PDF:
        assignments = tuple(value for group in groups for value in group.assignment_ids)
        pages = tuple(value for group in groups for value in group.page_ids)
        selected = names[0]
        return (
            PlannedPackagePdf(
                output_key=selected.output_key,
                position=selected.position,
                requirement_id=None,
                assignment_ids=assignments,
                page_ids=pages,
                filename=selected.filename,
                collision_index=selected.collision_index,
            ),
        )
    return tuple(
        PlannedPackagePdf(
            output_key=name.output_key,
            position=name.position,
            requirement_id=group.requirement_id,
            assignment_ids=group.assignment_ids,
            page_ids=group.page_ids,
            filename=name.filename,
            collision_index=name.collision_index,
        )
        for group, name in zip(groups, names, strict=True)
    )


def _output_facts(
    snapshot: ManifestSnapshot,
    document: dict[str, object],
    mode: PortalOutputMode,
    pdfs: tuple[PlannedPackagePdf, ...],
) -> OutputPlanPreflightFacts:
    raw_pages = document.get("pages")
    raw_overlays = document.get("overlays")
    if type(raw_pages) is not list or type(raw_overlays) is not list:
        reject_package_planner(PackagePlannerErrorCode.MANIFEST_INVALID, "package.overlays")
    try:
        known_pages = {
            _parse_uuid4(cast(dict[str, object], value).get("page_id")) for value in raw_pages
        }
        overlay_pages = {
            _parse_uuid4(cast(dict[str, object], value).get("page_id")) for value in raw_overlays
        }
    except AttributeError, TypeError, ValueError:
        reject_package_planner(PackagePlannerErrorCode.MANIFEST_INVALID, "package.overlays")
    if not overlay_pages <= known_pages:
        reject_package_planner(PackagePlannerErrorCode.MANIFEST_INVALID, "package.overlay_pages")
    ordered_pages = tuple(page_id for pdf in pdfs for page_id in pdf.page_ids)
    flattened = tuple(page_id for page_id in ordered_pages if page_id in overlay_pages)
    try:
        return OutputPlanPreflightFacts(
            case_id=snapshot.case_id,
            manifest_revision=snapshot.revision,
            availability=OutputPlanAvailability.CURRENT,
            output_mode=mode.value,
            items=tuple(
                OutputItemPreflightFact(
                    output_key=pdf.output_key,
                    position=pdf.position,
                    page_ids=pdf.page_ids,
                    predicted_bytes=None,
                )
                for pdf in pdfs
            ),
            overlay_flattening_availability=OutputOverlayFlatteningAvailability.CURRENT,
            flattened_overlay_page_ids=flattened,
        )
    except PreflightError:
        reject_package_planner(PackagePlannerErrorCode.MODEL_INVALID, "package.output_facts")


def _semantic_fingerprint(
    binding: TemplateBinding,
    mode: PortalOutputMode,
    constraints: ActivePackageConstraints,
    pdfs: tuple[PlannedPackagePdf, ...],
    archive: PlannedPackageArchive | None,
    plan_version: int,
) -> str:
    values = constraints.values
    material = {
        "fingerprint_version": PACKAGE_EXPORT_SEMANTIC_FINGERPRINT_VERSION,
        "plan_version": plan_version,
        "template_binding": binding.to_manifest(),
        "mode": mode.value,
        "constraints": {
            "constraints_version": values.constraints_version,
            "allowed_output_modes": [value.value for value in values.allowed_modes],
            "encryption_policy": values.encryption_policy.value,
            "maximum_file_count": values.maximum_file_count,
            "maximum_total_bytes": values.maximum_total_bytes,
            "maximum_file_bytes": values.maximum_file_bytes,
            "maximum_pages_per_file": values.maximum_pages_per_file,
            "requested_output_names": list(constraints.requested_output_names),
        },
        "pdfs": [
            {
                "output_key": pdf.output_key,
                "position": pdf.position,
                "requirement_id": pdf.requirement_id,
                "assignment_ids": [str(value) for value in pdf.assignment_ids],
                "page_ids": [str(value) for value in pdf.page_ids],
                "filename": pdf.filename,
                "collision_index": pdf.collision_index,
            }
            for pdf in pdfs
        ],
        "archive": (
            None
            if archive is None
            else {
                "output_key": archive.output_key,
                "filename": archive.filename,
                "collision_index": archive.collision_index,
                "member_output_keys": list(archive.member_output_keys),
            }
        ),
    }
    encoded = json.dumps(
        material,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _parse_uuid4(value: object) -> UUID:
    if type(value) is not str:
        raise TypeError
    parsed = UUID(value)
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError
    return parsed


def _is_uuid4(value: object) -> bool:
    return type(value) is UUID and value.version == 4


def _is_token(value: object) -> bool:
    return type(value) is str and 1 <= len(value) <= 128 and _TOKEN.fullmatch(value) is not None


__all__ = (
    "ActivePackageConstraints",
    "PACKAGE_EXPORT_PLAN_VERSION",
    "PACKAGE_EXPORT_SEMANTIC_FINGERPRINT_VERSION",
    "PackageExportPlan",
    "PackagePlanAuthority",
    "PackagePlannerError",
    "PackagePlannerErrorCode",
    "PlannedPackageArchive",
    "PlannedPackagePdf",
    "build_package_export_plan",
)

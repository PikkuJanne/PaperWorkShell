"""Pure portal constraints and Windows-safe output-name rules.

The module evaluates only immutable, path-free observations.  It does not probe
the filesystem, interpret a template expression, or claim that a portal will
accept an output.  Template v1's filename pattern is mapped to one fixed code
algorithm rather than executed.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, cast

from paperworkshell.domain.checklist import TemplateBinding
from paperworkshell.domain.manifest_validation import MAX_OUTPUT_NAME_UTF16_CODE_UNITS
from paperworkshell.domain.pdf_inspection import PdfEncryptionState
from paperworkshell.preflight.facts import OutputPlanAvailability, OutputPlanPreflightFacts
from paperworkshell.preflight.models import (
    MAX_PREFLIGHT_SCOPE_POSITION,
    MAX_PREFLIGHT_STATEMENT_VERSION,
    PreflightError,
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
from paperworkshell.templates.models import SUPPORTED_TEMPLATE_SCHEMA_VERSION, TemplateSnapshot

PORTAL_CONSTRAINTS_VERSION: Final = 1
PORTAL_FILENAME_POLICY_VERSION: Final = 1
PORTAL_CONSTRAINT_RULE_VERSION: Final = 1
MAX_PORTAL_OUTPUTS: Final = MAX_PREFLIGHT_SCOPE_POSITION
MAX_WINDOWS_DESTINATION_PATH_UTF16_CODE_UNITS: Final = 32_767
MAX_TEMPLATE_FILENAME_STEM_CODEPOINTS: Final = 80

_TOKEN: Final = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*\Z")
_STATIC_STEM: Final = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_WINDOWS_FORBIDDEN_FILENAME_CHARACTERS: Final = frozenset('<>:"/\\|?*%')
_WINDOWS_RESERVED_STEMS: Final = frozenset(
    {
        "aux",
        "clock$",
        "con",
        "conin$",
        "conout$",
        "nul",
        "prn",
        *(f"com{number}" for number in range(1, 10)),
        *(f"lpt{number}" for number in range(1, 10)),
        "com¹",
        "com²",
        "com³",
        "lpt¹",
        "lpt²",
        "lpt³",
    }
)


class PortalOutputMode(StrEnum):
    """Closed output organizations already released by template v1."""

    COMBINED_PDF = "combined_pdf"
    SEPARATE_PDFS = "separate_pdfs"
    ZIP = "zip"


_OUTPUT_MODE_ORDER: Final[dict[PortalOutputMode, int]] = {
    PortalOutputMode.COMBINED_PDF: 0,
    PortalOutputMode.SEPARATE_PDFS: 1,
    PortalOutputMode.ZIP: 2,
}


class PortalEncryptionPolicy(StrEnum):
    """Closed output-encryption policies already released by template v1."""

    MUST_BE_UNENCRYPTED = "must_be_unencrypted"
    ALLOWED = "allowed"


@dataclass(frozen=True, slots=True, repr=False)
class PortalConstraints:
    """Validated template or manual constraint values ready to become active."""

    allowed_modes: tuple[PortalOutputMode, ...]
    encryption_policy: PortalEncryptionPolicy
    maximum_file_count: int | None = None
    maximum_total_bytes: int | None = None
    maximum_file_bytes: int | None = None
    maximum_pages_per_file: int | None = None
    default_mode: PortalOutputMode | None = None
    constraints_version: int = PORTAL_CONSTRAINTS_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.allowed_modes) is not tuple
            or not 1 <= len(self.allowed_modes) <= len(PortalOutputMode)
            or any(type(mode) is not PortalOutputMode for mode in self.allowed_modes)
            or len(set(self.allowed_modes)) != len(self.allowed_modes)
            or type(self.encryption_policy) is not PortalEncryptionPolicy
            or (
                self.default_mode is not None
                and (
                    type(self.default_mode) is not PortalOutputMode
                    or self.default_mode not in self.allowed_modes
                )
            )
            or type(self.constraints_version) is not int
            or self.constraints_version != PORTAL_CONSTRAINTS_VERSION
        ):
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "portal.constraints")
        for value, maximum in (
            (self.maximum_file_count, MAX_PREFLIGHT_STATEMENT_VERSION),
            (self.maximum_total_bytes, MAX_PREFLIGHT_STATEMENT_VERSION),
            (self.maximum_file_bytes, MAX_PREFLIGHT_STATEMENT_VERSION),
            (self.maximum_pages_per_file, MAX_PREFLIGHT_STATEMENT_VERSION),
        ):
            if value is not None and (type(value) is not int or not 1 <= value <= maximum):
                reject_preflight(PreflightErrorCode.MODEL_INVALID, "portal.constraint_limit")
        object.__setattr__(
            self,
            "allowed_modes",
            tuple(sorted(self.allowed_modes, key=_OUTPUT_MODE_ORDER.__getitem__)),
        )

    def __repr__(self) -> str:
        return (
            "PortalConstraints("
            f"allowed_modes={self.allowed_modes!r}, "
            f"encryption_policy={self.encryption_policy.value!r}, "
            f"has_file_count_limit={self.maximum_file_count is not None}, "
            f"has_total_size_limit={self.maximum_total_bytes is not None}, "
            f"has_file_size_limit={self.maximum_file_bytes is not None}, "
            f"has_page_limit={self.maximum_pages_per_file is not None})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class RequirementFilenameStem:
    """One inert template requirement-to-stem declaration."""

    requirement_id: str
    stem: str = field(repr=False)

    def __post_init__(self) -> None:
        _require_token(self.requirement_id, "portal.requirement_id")
        _require_template_stem(self.stem, "portal.requirement_stem")

    def __repr__(self) -> str:
        return f"RequirementFilenameStem(requirement_id={self.requirement_id!r})"


@dataclass(frozen=True, slots=True, repr=False)
class PortalFilenamePolicy:
    """Template v1's single fixed, non-executable filename policy."""

    package_stem: str = field(repr=False)
    requirement_stems: tuple[RequirementFilenameStem, ...] = field(repr=False)
    policy_version: int = PORTAL_FILENAME_POLICY_VERSION

    def __post_init__(self) -> None:
        _require_template_stem(self.package_stem, "portal.package_stem")
        if (
            type(self.requirement_stems) is not tuple
            or len(self.requirement_stems) > 128
            or any(type(item) is not RequirementFilenameStem for item in self.requirement_stems)
            or len({item.requirement_id for item in self.requirement_stems})
            != len(self.requirement_stems)
            or type(self.policy_version) is not int
            or self.policy_version != PORTAL_FILENAME_POLICY_VERSION
        ):
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "portal.filename_policy")
        comparison_keys = [self.package_stem.casefold()]
        comparison_keys.extend(item.stem.casefold() for item in self.requirement_stems)
        if len(set(comparison_keys)) != len(comparison_keys):
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "portal.filename_stems")

    def __repr__(self) -> str:
        return f"PortalFilenamePolicy(requirement_stem_count={len(self.requirement_stems)})"


@dataclass(frozen=True, slots=True, repr=False)
class PortalConstraintProfile:
    """Exact template-bound constraints plus its inert filename declarations."""

    binding: TemplateBinding = field(repr=False)
    constraints: PortalConstraints
    filename_policy: PortalFilenamePolicy = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.binding) is not TemplateBinding
            or type(self.constraints) is not PortalConstraints
            or type(self.filename_policy) is not PortalFilenamePolicy
        ):
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "portal.profile")

    def __repr__(self) -> str:
        return (
            "PortalConstraintProfile("
            f"allowed_mode_count={len(self.constraints.allowed_modes)}, "
            f"requirement_stem_count={len(self.filename_policy.requirement_stems)})"
        )


def portal_constraint_profile_from_template(snapshot: TemplateSnapshot) -> PortalConstraintProfile:
    """Convert one exact validated template snapshot without interpreting its pattern."""

    if (
        type(snapshot) is not TemplateSnapshot
        or snapshot.schema_version != SUPPORTED_TEMPLATE_SCHEMA_VERSION
    ):
        reject_preflight(PreflightErrorCode.MODEL_INVALID, "portal.template")
    document = snapshot.document
    try:
        raw_output = _exact_dict(document["output"])
        raw_limits = _exact_dict(raw_output["limits"])
        raw_filename_policy = _exact_dict(raw_output["filename_policy"])
        raw_modes = _exact_list(raw_output["allowed_modes"])
        raw_stems = _exact_list(raw_filename_policy["requirement_stems"])
        raw_requirements = _exact_list(document["requirements"])
        if (
            raw_output["output_version"] != 1
            or raw_filename_policy["policy_version"] != PORTAL_FILENAME_POLICY_VERSION
            or raw_filename_policy["pattern"] != "safe_static_stem_with_counter_v1"
            or raw_filename_policy["collision_policy"] != "append_counter"
            or raw_filename_policy["extension_policy"] != "derive_from_output_mode"
        ):
            raise ValueError
        allowed_modes = tuple(_portal_output_mode(value) for value in raw_modes)
        default_mode = _portal_output_mode(raw_output["default_mode"])
        encryption_policy = _portal_encryption_policy(raw_output["encryption_policy"])
        requirement_stems = tuple(
            RequirementFilenameStem(
                requirement_id=_exact_str(_exact_dict(value)["requirement_id"]),
                stem=_exact_str(_exact_dict(value)["stem"]),
            )
            for value in raw_stems
        )
        requirement_ids = {
            _exact_str(_exact_dict(value)["requirement_id"]) for value in raw_requirements
        }
        constraints = PortalConstraints(
            allowed_modes=allowed_modes,
            encryption_policy=encryption_policy,
            maximum_file_count=_exact_int(raw_limits["maximum_file_count"]),
            maximum_total_bytes=_exact_int(raw_limits["maximum_total_bytes"]),
            maximum_file_bytes=_exact_int(raw_limits["maximum_file_bytes"]),
            maximum_pages_per_file=_exact_int(raw_limits["maximum_pages_per_file"]),
            default_mode=default_mode,
        )
        filename_policy = PortalFilenamePolicy(
            package_stem=_exact_str(raw_filename_policy["package_stem"]),
            requirement_stems=requirement_stems,
        )
        stem_requirements = {item.requirement_id for item in requirement_stems}
        if PortalOutputMode.SEPARATE_PDFS in constraints.allowed_modes:
            if stem_requirements != requirement_ids:
                raise ValueError
        elif requirement_stems:
            raise ValueError
    except KeyError, TypeError, ValueError:
        reject_preflight(PreflightErrorCode.MODEL_INVALID, "portal.template_output")
    return PortalConstraintProfile(
        binding=TemplateBinding(
            template_id=snapshot.key.template_id,
            template_version=snapshot.key.template_version,
            sha256=snapshot.sha256,
        ),
        constraints=constraints,
        filename_policy=filename_policy,
    )


@dataclass(frozen=True, slots=True, repr=False)
class PortalFilenameRequest:
    """One ordered opaque output key and requested filename stem."""

    output_key: str
    position: int
    stem: str = field(repr=False)

    def __post_init__(self) -> None:
        _require_token(self.output_key, "portal.output_key")
        if type(self.position) is not int or not 1 <= self.position <= MAX_PORTAL_OUTPUTS:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "portal.output_position")
        object.__setattr__(self, "stem", _normalize_filename_stem(self.stem))

    def __repr__(self) -> str:
        return f"PortalFilenameRequest(position={self.position!r})"


@dataclass(frozen=True, slots=True, repr=False)
class ResolvedOutputFilename:
    """One deterministic safe result from the fixed counter algorithm."""

    output_key: str
    position: int
    filename: str = field(repr=False)
    collision_index: int

    def __post_init__(self) -> None:
        _require_token(self.output_key, "portal.output_key")
        if (
            type(self.position) is not int
            or not 1 <= self.position <= MAX_PORTAL_OUTPUTS
            or type(self.collision_index) is not int
            or not 1 <= self.collision_index <= MAX_PORTAL_OUTPUTS + 1
        ):
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "portal.resolved_name")
        object.__setattr__(self, "filename", normalize_output_filename(self.filename))

    def __repr__(self) -> str:
        return (
            "ResolvedOutputFilename("
            f"position={self.position!r}, collision_index={self.collision_index!r})"
        )


def normalize_output_filename(
    value: str,
    *,
    destination_parent_utf16_units: int = 0,
) -> str:
    """Return NFC filename text after strict component and numeric path checks."""

    _require_parent_path_units(destination_parent_utf16_units)
    if type(value) is not str:
        reject_preflight(PreflightErrorCode.MODEL_INVALID, "portal.filename")
    normalized = unicodedata.normalize("NFC", value)
    invalid = (
        not normalized
        or normalized in {".", ".."}
        or normalized.endswith((" ", "."))
        or any(character in _WINDOWS_FORBIDDEN_FILENAME_CHARACTERS for character in normalized)
        or any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in normalized)
    )
    units = _utf16_units(normalized, "portal.filename_unicode")
    stem = normalized.casefold().split(".", maxsplit=1)[0].rstrip(" .")
    if (
        invalid
        or stem in _WINDOWS_RESERVED_STEMS
        or units > MAX_OUTPUT_NAME_UTF16_CODE_UNITS
        or units > _available_filename_units(destination_parent_utf16_units)
    ):
        reject_preflight(PreflightErrorCode.MODEL_INVALID, "portal.filename")
    return normalized


def resolve_output_filenames(
    requests: tuple[PortalFilenameRequest, ...],
    mode: PortalOutputMode,
    *,
    destination_parent_utf16_units: int = 0,
) -> tuple[ResolvedOutputFilename, ...]:
    """Resolve ordered PDF outputs, including PDF members of a ZIP package."""

    _require_parent_path_units(destination_parent_utf16_units)
    if (
        type(requests) is not tuple
        or not 1 <= len(requests) <= MAX_PORTAL_OUTPUTS
        or any(type(item) is not PortalFilenameRequest for item in requests)
        or tuple(item.position for item in requests) != tuple(range(1, len(requests) + 1))
        or len({item.output_key for item in requests}) != len(requests)
        or type(mode) is not PortalOutputMode
    ):
        reject_preflight(PreflightErrorCode.MODEL_INVALID, "portal.filename_requests")
    return _resolve_filename_components(
        requests,
        extension=".pdf",
        destination_parent_utf16_units=destination_parent_utf16_units,
    )


def resolve_zip_archive_filename(
    request: PortalFilenameRequest,
    *,
    destination_parent_utf16_units: int = 0,
) -> ResolvedOutputFilename:
    """Resolve the single outer ZIP component in its separate filename namespace."""

    if type(request) is not PortalFilenameRequest or request.position != 1:
        reject_preflight(PreflightErrorCode.MODEL_INVALID, "portal.archive_filename_request")
    return _resolve_filename_components(
        (request,),
        extension=".zip",
        destination_parent_utf16_units=destination_parent_utf16_units,
    )[0]


def _resolve_filename_components(
    requests: tuple[PortalFilenameRequest, ...],
    *,
    extension: str,
    destination_parent_utf16_units: int,
) -> tuple[ResolvedOutputFilename, ...]:
    """Apply one fixed extension and understandable counters in stable order."""

    _require_parent_path_units(destination_parent_utf16_units)
    if (
        type(requests) is not tuple
        or not 1 <= len(requests) <= MAX_PORTAL_OUTPUTS
        or any(type(item) is not PortalFilenameRequest for item in requests)
        or tuple(item.position for item in requests) != tuple(range(1, len(requests) + 1))
        or len({item.output_key for item in requests}) != len(requests)
        or extension not in {".pdf", ".zip"}
    ):
        reject_preflight(PreflightErrorCode.MODEL_INVALID, "portal.filename_requests")
    maximum_units = min(
        MAX_OUTPUT_NAME_UTF16_CODE_UNITS,
        _available_filename_units(destination_parent_utf16_units),
    )
    used: set[str] = set()
    resolved: list[ResolvedOutputFilename] = []
    for request in requests:
        collision_index = 1
        candidate = _render_filename(
            request.stem,
            extension=extension,
            suffix="",
            maximum_units=maximum_units,
            truncate=False,
        )
        while _filename_comparison_key(candidate) in used:
            collision_index += 1
            if collision_index > len(requests) + 1:
                reject_preflight(PreflightErrorCode.LIMIT_EXCEEDED, "portal.filename_collisions")
            candidate = _render_filename(
                request.stem,
                extension=extension,
                suffix=f"-{collision_index}",
                maximum_units=maximum_units,
                truncate=True,
            )
        candidate = normalize_output_filename(
            candidate,
            destination_parent_utf16_units=destination_parent_utf16_units,
        )
        used.add(_filename_comparison_key(candidate))
        resolved.append(
            ResolvedOutputFilename(
                output_key=request.output_key,
                position=request.position,
                filename=candidate,
                collision_index=collision_index,
            )
        )
    return tuple(resolved)


@dataclass(frozen=True, slots=True, repr=False)
class PortalOutputObservation:
    """Path-free output facts not present in PWS-0404's generic plan projection."""

    output_key: str
    position: int
    filename: str = field(repr=False)
    encryption_state: PdfEncryptionState

    def __post_init__(self) -> None:
        _require_token(self.output_key, "portal.output_key")
        if (
            type(self.position) is not int
            or not 1 <= self.position <= MAX_PORTAL_OUTPUTS
            or type(self.encryption_state) is not PdfEncryptionState
        ):
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "portal.output_observation")
        object.__setattr__(self, "filename", normalize_output_filename(self.filename))

    def __repr__(self) -> str:
        return (
            "PortalOutputObservation("
            f"position={self.position!r}, encryption_state={self.encryption_state.value!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class PortalArchiveObservation:
    """Outer ZIP artifact facts kept separate from its planned PDF members."""

    filename: str = field(repr=False)
    predicted_bytes: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "filename", normalize_output_filename(self.filename))
        if self.predicted_bytes is not None and (
            type(self.predicted_bytes) is not int
            or not 0 <= self.predicted_bytes <= MAX_PREFLIGHT_STATEMENT_VERSION
        ):
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "portal.archive_observation")

    def __repr__(self) -> str:
        return f"PortalArchiveObservation(has_predicted_bytes={self.predicted_bytes is not None})"


@dataclass(frozen=True, slots=True, repr=False)
class PortalConstraintEvaluation:
    """Bounded pure inputs for one output-plan constraint evaluation."""

    constraints: PortalConstraints
    plan: OutputPlanPreflightFacts = field(repr=False)
    outputs: tuple[PortalOutputObservation, ...] = field(repr=False)
    archive: PortalArchiveObservation | None = field(default=None, repr=False)
    destination_parent_utf16_units: int = field(default=0, repr=False)

    def __post_init__(self) -> None:
        _require_parent_path_units(self.destination_parent_utf16_units)
        if (
            type(self.constraints) is not PortalConstraints
            or type(self.plan) is not OutputPlanPreflightFacts
            or type(self.outputs) is not tuple
            or len(self.outputs) > MAX_PORTAL_OUTPUTS
            or any(type(item) is not PortalOutputObservation for item in self.outputs)
            or (self.archive is not None and type(self.archive) is not PortalArchiveObservation)
        ):
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "portal.evaluation")
        if self.plan.availability is OutputPlanAvailability.CURRENT:
            expected = tuple((item.output_key, item.position) for item in self.plan.items)
            observed = tuple((item.output_key, item.position) for item in self.outputs)
            if observed != expected:
                reject_preflight(PreflightErrorCode.MODEL_INVALID, "portal.output_binding")
            selected_mode = _known_output_mode(self.plan.output_mode)
            member_parent_units = (
                0 if selected_mode is PortalOutputMode.ZIP else self.destination_parent_utf16_units
            )
            for output in self.outputs:
                normalize_output_filename(
                    output.filename,
                    destination_parent_utf16_units=member_parent_units,
                )
            if selected_mode is not PortalOutputMode.ZIP and self.archive is not None:
                reject_preflight(PreflightErrorCode.MODEL_INVALID, "portal.archive_binding")
            if self.archive is not None:
                normalize_output_filename(
                    self.archive.filename,
                    destination_parent_utf16_units=self.destination_parent_utf16_units,
                )
        elif self.outputs or self.archive is not None:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "portal.output_binding")

    def __repr__(self) -> str:
        return (
            "PortalConstraintEvaluation("
            f"availability={self.plan.availability.value!r}, output_count={len(self.outputs)}, "
            f"has_archive={self.archive is not None})"
        )


def _finding_definition(
    code: str,
    message: str,
    remediation: str,
) -> PreflightFindingDefinition:
    return PreflightFindingDefinition(
        code=code,
        severity=PreflightSeverity.BLOCKING_ERROR,
        remediation_action=PreflightRemediationAction.REVIEW_OUTPUT_PLAN,
        message=message,
        remediation=remediation,
    )


PORTAL_CONSTRAINT_FINDING_DEFINITIONS: Final = tuple(
    sorted(
        (
            _finding_definition(
                "PORTAL_ENCRYPTION_DETECTED",
                "An output is encrypted, but the active constraint requires unencrypted output.",
                "Create an unencrypted derivative and run preflight again.",
            ),
            _finding_definition(
                "PORTAL_ENCRYPTION_UNVERIFIED",
                "The encryption state of an output has not been verified.",
                "Verify that the output is unencrypted and run preflight again.",
            ),
            _finding_definition(
                "PORTAL_EXTENSION_INVALID",
                "An output filename does not use the extension required by the selected output mode.",
                "Review the output name and selected output mode.",
            ),
            _finding_definition(
                "PORTAL_FILENAME_COLLISION",
                "Two output filenames collide under Windows filename comparison rules.",
                "Apply the deterministic filename counter and review the resulting names.",
            ),
            _finding_definition(
                "PORTAL_FILE_COUNT_EXCEEDED",
                "The planned number of output files is above the active limit.",
                "Reduce or regroup the planned output files.",
            ),
            _finding_definition(
                "PORTAL_FILE_SIZE_EXCEEDED",
                "The estimated size of an output file is above the active per-file limit.",
                "Reduce the output size or review the active limit.",
            ),
            _finding_definition(
                "PORTAL_FILE_SIZE_UNAVAILABLE",
                "The estimated size of an output file is not available, so its limit was not checked.",
                "Refresh the output estimate and run preflight again.",
            ),
            _finding_definition(
                "PORTAL_OUTPUT_MODE_NOT_ALLOWED",
                "The selected output mode is not allowed by the active constraints.",
                "Select an allowed output mode or review the active constraints.",
            ),
            _finding_definition(
                "PORTAL_OUTPUT_ORGANIZATION_INVALID",
                "The output grouping does not match the selected output mode.",
                "Regenerate the output plan for the selected mode.",
            ),
            _finding_definition(
                "PORTAL_OUTPUT_PLAN_UNAVAILABLE",
                "A current output plan is not available, so portal constraints were not checked.",
                "Create or refresh the output plan and run preflight again.",
            ),
            _finding_definition(
                "PORTAL_PAGE_COUNT_EXCEEDED",
                "The planned page count of an output file is above the active per-file limit.",
                "Reduce or regroup the pages in that output.",
            ),
            _finding_definition(
                "PORTAL_TOTAL_SIZE_EXCEEDED",
                "The estimated total output size is above the active limit.",
                "Reduce the total output size or review the active limit.",
            ),
            _finding_definition(
                "PORTAL_TOTAL_SIZE_UNAVAILABLE",
                "The estimated total output size is not available, so its limit was not checked.",
                "Refresh all output estimates and run preflight again.",
            ),
            _finding_definition(
                "PORTAL_ZIP_ARCHIVE_UNAVAILABLE",
                "The selected ZIP output has no exact outer-archive observation.",
                "Create or refresh the ZIP package plan and run preflight again.",
            ),
        ),
        key=lambda definition: definition.code,
    )
)


def evaluate_portal_constraints(
    evaluation: PortalConstraintEvaluation,
) -> tuple[PreflightFindingTarget, ...]:
    """Return deterministic typed findings without an acceptance claim."""

    if type(evaluation) is not PortalConstraintEvaluation:
        reject_preflight(PreflightErrorCode.MODEL_INVALID, "portal.evaluation")
    plan = evaluation.plan
    case_scope = PreflightScope(PreflightScopeKind.CASE, plan.case_id)
    if plan.availability is not OutputPlanAvailability.CURRENT:
        return (PreflightFindingTarget("PORTAL_OUTPUT_PLAN_UNAVAILABLE", case_scope),)

    constraints = evaluation.constraints
    selected_mode = _known_output_mode(plan.output_mode)
    targets: list[PreflightFindingTarget] = []
    if selected_mode is None or selected_mode not in constraints.allowed_modes:
        targets.append(PreflightFindingTarget("PORTAL_OUTPUT_MODE_NOT_ALLOWED", case_scope))
    if selected_mode is PortalOutputMode.COMBINED_PDF and len(plan.items) != 1:
        targets.append(PreflightFindingTarget("PORTAL_OUTPUT_ORGANIZATION_INVALID", case_scope))
    if selected_mode is PortalOutputMode.ZIP and evaluation.archive is None:
        targets.append(PreflightFindingTarget("PORTAL_ZIP_ARCHIVE_UNAVAILABLE", case_scope))

    top_level_file_count = (
        len(plan.items)
        if selected_mode is PortalOutputMode.SEPARATE_PDFS
        else 1
        if selected_mode in {PortalOutputMode.COMBINED_PDF, PortalOutputMode.ZIP}
        else len(plan.items)
    )

    if (
        constraints.maximum_file_count is not None
        and top_level_file_count > constraints.maximum_file_count
    ):
        targets.append(
            PreflightFindingTarget(
                "PORTAL_FILE_COUNT_EXCEEDED",
                case_scope,
                evidence=(
                    PreflightEvidence(
                        "file_count",
                        top_level_file_count,
                        PreflightEvidenceUnit.FILES,
                        constraints.maximum_file_count,
                    ),
                ),
            )
        )

    predicted_sizes = tuple(item.predicted_bytes for item in plan.items)
    if constraints.maximum_total_bytes is not None:
        if selected_mode is PortalOutputMode.ZIP:
            total_bytes = None if evaluation.archive is None else evaluation.archive.predicted_bytes
        else:
            total_bytes = (
                None
                if any(value is None for value in predicted_sizes)
                else sum(cast(int, value) for value in predicted_sizes)
            )
        if total_bytes is None:
            targets.append(PreflightFindingTarget("PORTAL_TOTAL_SIZE_UNAVAILABLE", case_scope))
        elif total_bytes > constraints.maximum_total_bytes:
            targets.append(
                PreflightFindingTarget(
                    "PORTAL_TOTAL_SIZE_EXCEEDED",
                    case_scope,
                    evidence=(
                        PreflightEvidence(
                            "total_bytes_lower_bound"
                            if total_bytes > MAX_PREFLIGHT_STATEMENT_VERSION
                            else "total_bytes",
                            min(total_bytes, MAX_PREFLIGHT_STATEMENT_VERSION),
                            PreflightEvidenceUnit.BYTES,
                            constraints.maximum_total_bytes,
                        ),
                    ),
                )
            )

    archive = evaluation.archive
    if selected_mode is PortalOutputMode.ZIP and archive is not None:
        if constraints.maximum_file_bytes is not None:
            if archive.predicted_bytes is None:
                targets.append(PreflightFindingTarget("PORTAL_FILE_SIZE_UNAVAILABLE", case_scope))
            elif archive.predicted_bytes > constraints.maximum_file_bytes:
                targets.append(
                    PreflightFindingTarget(
                        "PORTAL_FILE_SIZE_EXCEEDED",
                        case_scope,
                        evidence=(
                            PreflightEvidence(
                                "archive_bytes",
                                archive.predicted_bytes,
                                PreflightEvidenceUnit.BYTES,
                                constraints.maximum_file_bytes,
                            ),
                        ),
                    )
                )
        if not archive.filename.casefold().endswith(".zip"):
            targets.append(PreflightFindingTarget("PORTAL_EXTENSION_INVALID", case_scope))

    first_scope_by_name: dict[str, PreflightScope] = {}
    expected_extension = None if selected_mode is None else ".pdf"
    for item, output in zip(plan.items, evaluation.outputs, strict=True):
        scope = PreflightScope(PreflightScopeKind.OUTPUT, item.output_key, item.position)
        if constraints.maximum_file_bytes is not None:
            if item.predicted_bytes is None:
                targets.append(PreflightFindingTarget("PORTAL_FILE_SIZE_UNAVAILABLE", scope))
            elif item.predicted_bytes > constraints.maximum_file_bytes:
                targets.append(
                    PreflightFindingTarget(
                        "PORTAL_FILE_SIZE_EXCEEDED",
                        scope,
                        evidence=(
                            PreflightEvidence(
                                "file_bytes",
                                item.predicted_bytes,
                                PreflightEvidenceUnit.BYTES,
                                constraints.maximum_file_bytes,
                            ),
                        ),
                    )
                )
        if (
            constraints.maximum_pages_per_file is not None
            and len(item.page_ids) > constraints.maximum_pages_per_file
        ):
            targets.append(
                PreflightFindingTarget(
                    "PORTAL_PAGE_COUNT_EXCEEDED",
                    scope,
                    evidence=(
                        PreflightEvidence(
                            "page_count",
                            len(item.page_ids),
                            PreflightEvidenceUnit.PAGES,
                            constraints.maximum_pages_per_file,
                        ),
                    ),
                )
            )
        if expected_extension is not None and not output.filename.casefold().endswith(
            expected_extension
        ):
            targets.append(PreflightFindingTarget("PORTAL_EXTENSION_INVALID", scope))
        if constraints.encryption_policy is PortalEncryptionPolicy.MUST_BE_UNENCRYPTED:
            if output.encryption_state is PdfEncryptionState.ENCRYPTED:
                targets.append(PreflightFindingTarget("PORTAL_ENCRYPTION_DETECTED", scope))
            elif output.encryption_state is PdfEncryptionState.UNKNOWN:
                targets.append(PreflightFindingTarget("PORTAL_ENCRYPTION_UNVERIFIED", scope))
        comparison_key = _filename_comparison_key(output.filename)
        prior_scope = first_scope_by_name.get(comparison_key)
        if prior_scope is None:
            first_scope_by_name[comparison_key] = scope
        else:
            targets.append(
                PreflightFindingTarget(
                    "PORTAL_FILENAME_COLLISION",
                    scope,
                    related_scope=prior_scope,
                )
            )
    return tuple(
        sorted(
            targets,
            key=lambda target: (
                0 if target.scope.position is None else target.scope.position,
                target.code,
                "" if target.related_scope is None else str(target.related_scope.item_id),
            ),
        )
    )


def _require_token(value: object, context: str) -> str:
    if type(value) is not str or not 1 <= len(value) <= 128 or _TOKEN.fullmatch(value) is None:
        reject_preflight(PreflightErrorCode.MODEL_INVALID, context)
    return value


def _require_template_stem(value: object, context: str) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= MAX_TEMPLATE_FILENAME_STEM_CODEPOINTS
        or _STATIC_STEM.fullmatch(value) is None
        or value != unicodedata.normalize("NFC", value)
        or value.casefold().split(".", maxsplit=1)[0] in _WINDOWS_RESERVED_STEMS
    ):
        reject_preflight(PreflightErrorCode.MODEL_INVALID, context)
    return value


def _normalize_filename_stem(value: object) -> str:
    if type(value) is not str:
        reject_preflight(PreflightErrorCode.MODEL_INVALID, "portal.filename_stem")
    normalized = unicodedata.normalize("NFC", value)
    # Reuse the final-component checks; a stem is itself a Windows component.
    try:
        return normalize_output_filename(normalized)
    except PreflightError:
        reject_preflight(PreflightErrorCode.MODEL_INVALID, "portal.filename_stem")


def _require_parent_path_units(value: object) -> int:
    if type(value) is not int or not 0 <= value < MAX_WINDOWS_DESTINATION_PATH_UTF16_CODE_UNITS:
        reject_preflight(PreflightErrorCode.MODEL_INVALID, "portal.parent_path_length")
    return value


def _available_filename_units(parent_units: int) -> int:
    separator_units = 0 if parent_units == 0 else 1
    available = MAX_WINDOWS_DESTINATION_PATH_UTF16_CODE_UNITS - parent_units - separator_units
    if available < 1:
        reject_preflight(PreflightErrorCode.MODEL_INVALID, "portal.path_length")
    return available


def _utf16_units(value: str, context: str) -> int:
    try:
        return len(value.encode("utf-16-le")) // 2
    except UnicodeEncodeError:
        reject_preflight(PreflightErrorCode.MODEL_INVALID, context)


def _filename_comparison_key(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _known_output_mode(value: str) -> PortalOutputMode | None:
    try:
        return PortalOutputMode(value)
    except ValueError:
        return None


def _render_filename(
    stem: str,
    *,
    extension: str,
    suffix: str,
    maximum_units: int,
    truncate: bool,
) -> str:
    reserved_units = _utf16_units(suffix + extension, "portal.filename_unicode")
    available_stem_units = maximum_units - reserved_units
    if available_stem_units < 1:
        reject_preflight(PreflightErrorCode.MODEL_INVALID, "portal.path_length")
    selected_stem = stem
    if _utf16_units(selected_stem, "portal.filename_unicode") > available_stem_units:
        if not truncate:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "portal.filename_length")
        selected_stem = _truncate_utf16(selected_stem, available_stem_units).rstrip(" .")
        if not selected_stem:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "portal.filename_length")
    return f"{selected_stem}{suffix}{extension}"


def _truncate_utf16(value: str, maximum_units: int) -> str:
    units = 0
    characters: list[str] = []
    for character in value:
        character_units = 2 if ord(character) > 0xFFFF else 1
        if units + character_units > maximum_units:
            break
        characters.append(character)
        units += character_units
    return unicodedata.normalize("NFC", "".join(characters))


def _exact_dict(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise TypeError
    return cast(dict[str, object], value)


def _exact_list(value: object) -> list[object]:
    if type(value) is not list:
        raise TypeError
    return cast(list[object], value)


def _exact_str(value: object) -> str:
    if type(value) is not str:
        raise TypeError
    return value


def _exact_int(value: object) -> int:
    if type(value) is not int:
        raise TypeError
    return value


def _portal_output_mode(value: object) -> PortalOutputMode:
    if type(value) is not str:
        raise TypeError
    return PortalOutputMode(value)


def _portal_encryption_policy(value: object) -> PortalEncryptionPolicy:
    if type(value) is not str:
        raise TypeError
    return PortalEncryptionPolicy(value)


__all__ = (
    "MAX_PORTAL_OUTPUTS",
    "MAX_TEMPLATE_FILENAME_STEM_CODEPOINTS",
    "MAX_WINDOWS_DESTINATION_PATH_UTF16_CODE_UNITS",
    "PORTAL_CONSTRAINT_FINDING_DEFINITIONS",
    "PORTAL_CONSTRAINT_RULE_VERSION",
    "PORTAL_CONSTRAINTS_VERSION",
    "PORTAL_FILENAME_POLICY_VERSION",
    "PortalArchiveObservation",
    "PortalConstraintEvaluation",
    "PortalConstraintProfile",
    "PortalConstraints",
    "PortalEncryptionPolicy",
    "PortalFilenamePolicy",
    "PortalFilenameRequest",
    "PortalOutputMode",
    "PortalOutputObservation",
    "RequirementFilenameStem",
    "ResolvedOutputFilename",
    "evaluate_portal_constraints",
    "normalize_output_filename",
    "portal_constraint_profile_from_template",
    "resolve_output_filenames",
    "resolve_zip_archive_filename",
)

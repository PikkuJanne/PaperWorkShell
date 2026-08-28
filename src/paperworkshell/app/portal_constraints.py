"""Detached application workflow for editing portal packaging constraints.

The public editor state is deliberately path-free.  Manifest documents, CAS
tokens, template digests, and storage authority remain private to the workflow
service.  Requested output names are validated as complete Windows-safe
components before they can enter a state or a manifest candidate.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import date
from enum import StrEnum
from typing import Final, Never, cast
from uuid import UUID, uuid4

from paperworkshell.domain.manifest_repository import (
    CURRENT_MANIFEST_SCHEMA_VERSION,
    MAX_SAFE_MANIFEST_INTEGER,
    ManifestSnapshot,
)
from paperworkshell.preflight.models import PreflightError
from paperworkshell.preflight.portal_constraints import (
    MAX_PORTAL_OUTPUTS,
    PortalConstraintProfile,
    PortalConstraints,
    PortalEncryptionPolicy,
    PortalFilenameRequest,
    PortalOutputMode,
    ResolvedOutputFilename,
    normalize_output_filename,
    portal_constraint_profile_from_template,
    resolve_output_filenames,
    resolve_zip_archive_filename,
)
from paperworkshell.templates.loader import SafeTemplateLoader
from paperworkshell.templates.models import TemplateKey, TemplateLoadError, TemplateSnapshot

_CONTEXT: Final = re.compile(r"[a-z][a-z0-9_.]{0,63}\Z")
_MAX_EDITOR_ID_ATTEMPTS: Final = 16


class ConstraintSourceKind(StrEnum):
    """Closed, textual origins shown by the editor."""

    GENERIC_TEMPLATE = "generic_template"
    CASE_OVERRIDE = "case_override"
    MANUAL = "manual"
    GENERIC_DEFAULTS_DRAFT = "generic_defaults_draft"


class ConstraintValidationField(StrEnum):
    """Stable focus targets for one rejected full draft."""

    ALLOWED_MODES = "allowed_modes"
    ENCRYPTION_POLICY = "encryption_policy"
    MAXIMUM_FILE_COUNT = "maximum_file_count"
    MAXIMUM_TOTAL_BYTES = "maximum_total_bytes"
    MAXIMUM_FILE_BYTES = "maximum_file_bytes"
    MAXIMUM_PAGES_PER_FILE = "maximum_pages_per_file"
    OUTPUT_NAMES = "output_names"


class ConstraintEditorErrorCode(StrEnum):
    """Closed application failures with privacy-safe user messages."""

    MODEL_INVALID = "PWS_CONSTRAINT_MODEL_INVALID"
    DRAFT_INVALID = "PWS_CONSTRAINT_DRAFT_INVALID"
    WORKFLOW_ACTIVE = "PWS_CONSTRAINT_WORKFLOW_ACTIVE"
    SESSION_MISMATCH = "PWS_CONSTRAINT_SESSION_MISMATCH"
    TEMPLATE_UNAVAILABLE = "PWS_CONSTRAINT_TEMPLATE_UNAVAILABLE"
    MANIFEST_UNAVAILABLE = "PWS_CONSTRAINT_MANIFEST_UNAVAILABLE"
    MANIFEST_INVALID = "PWS_CONSTRAINT_MANIFEST_INVALID"
    REVISION_CONFLICT = "PWS_CONSTRAINT_REVISION_CONFLICT"
    SAVE_FAILED = "PWS_CONSTRAINT_SAVE_FAILED"
    ID_GENERATION_FAILED = "PWS_CONSTRAINT_ID_GENERATION_FAILED"


_ERROR_MESSAGES: Final[dict[ConstraintEditorErrorCode, str]] = {
    ConstraintEditorErrorCode.MODEL_INVALID: (
        "PaperWorkShell received invalid constraint editor data."
    ),
    ConstraintEditorErrorCode.DRAFT_INVALID: (
        "Review the highlighted constraint. No case constraint was changed."
    ),
    ConstraintEditorErrorCode.WORKFLOW_ACTIVE: (
        "Finish or cancel the open constraint editor before opening another one."
    ),
    ConstraintEditorErrorCode.SESSION_MISMATCH: (
        "This constraint draft no longer matches the open case revision. Reopen the editor."
    ),
    ConstraintEditorErrorCode.TEMPLATE_UNAVAILABLE: (
        "The configured generic template defaults are unavailable. No case constraint was changed."
    ),
    ConstraintEditorErrorCode.MANIFEST_UNAVAILABLE: (
        "PaperWorkShell could not read the case constraints safely."
    ),
    ConstraintEditorErrorCode.MANIFEST_INVALID: (
        "The stored case constraints are invalid and were not changed."
    ),
    ConstraintEditorErrorCode.REVISION_CONFLICT: (
        "The case changed before these constraints could be saved. Reopen the editor and try again."
    ),
    ConstraintEditorErrorCode.SAVE_FAILED: (
        "PaperWorkShell could not save the case constraints safely. The draft is retained."
    ),
    ConstraintEditorErrorCode.ID_GENERATION_FAILED: (
        "PaperWorkShell could not start a private constraint editing session."
    ),
}


@dataclass(slots=True, repr=False)
class ConstraintEditorError(ValueError):
    """Sanitized failure that never retains names, manifest facts, or raw exceptions."""

    code: ConstraintEditorErrorCode
    context: str
    field: ConstraintValidationField | None = None
    safe_message: str = dataclass_field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not ConstraintEditorErrorCode:
            self.code = ConstraintEditorErrorCode.MODEL_INVALID
        if type(self.context) is not str or _CONTEXT.fullmatch(self.context) is None:
            self.context = "constraints.editor"
        if self.field is not None and type(self.field) is not ConstraintValidationField:
            self.field = None
        self.safe_message = _ERROR_MESSAGES[self.code]
        self.args = (
            self.code.value,
            self.context,
            None if self.field is None else self.field.value,
            self.safe_message,
        )

    def __repr__(self) -> str:
        return (
            "ConstraintEditorError("
            f"code={self.code.value!r}, context={self.context!r}, "
            f"field={None if self.field is None else self.field.value!r})"
        )

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject_constraint_editor(
    code: ConstraintEditorErrorCode,
    context: str,
    field: ConstraintValidationField | None = None,
) -> Never:
    """Raise one closed failure without retaining an ambient exception chain."""

    error = ConstraintEditorError(code, context, field)
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None


class ConstraintAnnouncement(StrEnum):
    """Closed status text safe for assistive announcements and routine diagnostics."""

    READY = (
        "Review packaging constraints. These checks describe the configured limits; "
        "they do not guarantee portal acceptance."
    )
    DRAFT_VALIDATED = (
        "The complete draft passed local validation. Choose Apply to request saving it."
    )
    GENERIC_DEFAULTS_RESTORED = (
        "Configured generic template defaults were copied into this draft. "
        "Review them before Apply."
    )
    APPLYING = "Applying constraint changes. Keep this dialog open."
    APPLIED = "Constraint changes applied."
    APPLY_FAILED = (
        "PWS_CONSTRAINT_APPLY_FAILED: PaperWorkShell could not save constraint changes safely. "
        "The validated draft is retained; review it and try Apply again."
    )


@dataclass(frozen=True, slots=True, repr=False)
class ConstraintSourceView:
    """Path-free textual explanation of the draft's current source."""

    kind: ConstraintSourceKind
    heading: str = dataclass_field(repr=False)
    detail: str = dataclass_field(repr=False)
    template_title: str = dataclass_field(repr=False)
    can_restore_generic_defaults: bool

    def __post_init__(self) -> None:
        if (
            type(self.kind) is not ConstraintSourceKind
            or type(self.heading) is not str
            or not self.heading
            or type(self.detail) is not str
            or not self.detail
            or type(self.template_title) is not str
            or not self.template_title
            or type(self.can_restore_generic_defaults) is not bool
        ):
            reject_constraint_editor(
                ConstraintEditorErrorCode.MODEL_INVALID,
                "constraints.source",
            )

    def __repr__(self) -> str:
        return (
            "ConstraintSourceView("
            f"kind={self.kind.value!r}, "
            f"can_restore_generic_defaults={self.can_restore_generic_defaults!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class ConstraintFilenameView:
    """One ordered safe filename component and its derived, read-only suffix."""

    output_key: str
    position: int
    stem: str = dataclass_field(repr=False)
    extension: str
    filename: str = dataclass_field(repr=False)
    collision_index: int

    def __post_init__(self) -> None:
        try:
            request = PortalFilenameRequest(self.output_key, self.position, self.stem)
            normalized = normalize_output_filename(self.filename)
        except PreflightError:
            reject_constraint_editor(
                ConstraintEditorErrorCode.MODEL_INVALID,
                "constraints.filename_view",
                ConstraintValidationField.OUTPUT_NAMES,
            )
        if (
            request.stem != self.stem
            or normalized != self.filename
            or self.extension not in {".pdf", ".zip"}
            or not self.filename.casefold().endswith(self.extension)
            or type(self.collision_index) is not int
            or not 1 <= self.collision_index <= MAX_PORTAL_OUTPUTS + 1
        ):
            reject_constraint_editor(
                ConstraintEditorErrorCode.MODEL_INVALID,
                "constraints.filename_view",
                ConstraintValidationField.OUTPUT_NAMES,
            )

    def __repr__(self) -> str:
        return (
            "ConstraintFilenameView("
            f"position={self.position!r}, extension={self.extension!r}, "
            f"collision_index={self.collision_index!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class ConstraintDraftInput:
    """Complete detached form values; names are stems until fixed suffix derivation."""

    allowed_modes: tuple[PortalOutputMode, ...]
    encryption_policy: PortalEncryptionPolicy
    maximum_file_count: int | None
    maximum_total_bytes: int | None
    maximum_file_bytes: int | None
    maximum_pages_per_file: int | None
    requested_filename_stems: tuple[str, ...] = dataclass_field(repr=False)

    def __post_init__(self) -> None:
        if type(self.allowed_modes) is not tuple or any(
            type(value) is not PortalOutputMode for value in self.allowed_modes
        ):
            reject_constraint_editor(
                ConstraintEditorErrorCode.DRAFT_INVALID,
                "constraints.draft_modes",
                ConstraintValidationField.ALLOWED_MODES,
            )
        if type(self.encryption_policy) is not PortalEncryptionPolicy:
            reject_constraint_editor(
                ConstraintEditorErrorCode.DRAFT_INVALID,
                "constraints.draft_encryption",
                ConstraintValidationField.ENCRYPTION_POLICY,
            )
        if type(self.requested_filename_stems) is not tuple or any(
            type(value) is not str for value in self.requested_filename_stems
        ):
            reject_constraint_editor(
                ConstraintEditorErrorCode.DRAFT_INVALID,
                "constraints.draft_names",
                ConstraintValidationField.OUTPUT_NAMES,
            )

    def __repr__(self) -> str:
        return (
            "ConstraintDraftInput("
            f"allowed_mode_count={len(self.allowed_modes)}, "
            f"encryption_policy={self.encryption_policy.value!r}, "
            f"has_file_count_limit={self.maximum_file_count is not None}, "
            f"has_total_size_limit={self.maximum_total_bytes is not None}, "
            f"has_file_size_limit={self.maximum_file_bytes is not None}, "
            f"has_page_limit={self.maximum_pages_per_file is not None}, "
            f"requested_name_count={len(self.requested_filename_stems)})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class _PersistedConstraints:
    constraints: PortalConstraints
    requested_output_names: tuple[str, ...] = dataclass_field(repr=False)

    def to_document(self) -> dict[str, object]:
        document: dict[str, object] = {
            "constraints_version": self.constraints.constraints_version,
            "allowed_output_modes": [value.value for value in self.constraints.allowed_modes],
            "encryption_policy": self.constraints.encryption_policy.value,
        }
        for key, value in (
            ("maximum_file_count", self.constraints.maximum_file_count),
            ("maximum_total_bytes", self.constraints.maximum_total_bytes),
            ("maximum_file_bytes", self.constraints.maximum_file_bytes),
            ("maximum_pages_per_file", self.constraints.maximum_pages_per_file),
        ):
            if value is not None:
                document[key] = value
        if self.requested_output_names:
            document["requested_output_names"] = list(self.requested_output_names)
        return document


@dataclass(frozen=True, slots=True, repr=False)
class _TemplateDefaults:
    persisted: _PersistedConstraints
    filename_mode: PortalOutputMode
    filename_stems: tuple[str, ...] = dataclass_field(repr=False)
    template_title: str = dataclass_field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class ConstraintEditorState:
    """Immutable, path-free state suitable for a modal editor and Qt signals."""

    case_id: UUID
    manifest_revision: int
    editor_session_id: UUID
    source: ConstraintSourceView
    constraints: PortalConstraints
    filename_mode: PortalOutputMode
    output_names: tuple[ConstraintFilenameView, ...] = dataclass_field(repr=False)
    changed: bool
    announcement: ConstraintAnnouncement
    _persisted: _PersistedConstraints = dataclass_field(repr=False, compare=False)
    _opening: _PersistedConstraints = dataclass_field(repr=False, compare=False)
    _defaults: _TemplateDefaults = dataclass_field(repr=False, compare=False)
    _opening_marker: object = dataclass_field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if (
            not _is_uuid(self.case_id)
            or type(self.manifest_revision) is not int
            or not 0 <= self.manifest_revision <= MAX_SAFE_MANIFEST_INTEGER
            or not _is_uuid(self.editor_session_id)
            or type(self.source) is not ConstraintSourceView
            or type(self.constraints) is not PortalConstraints
            or type(self.filename_mode) is not PortalOutputMode
            or self.filename_mode not in self.constraints.allowed_modes
            or type(self.output_names) is not tuple
            or any(type(value) is not ConstraintFilenameView for value in self.output_names)
            or tuple(value.position for value in self.output_names)
            != tuple(range(1, len(self.output_names) + 1))
            or type(self.changed) is not bool
            or type(self.announcement) is not ConstraintAnnouncement
            or type(self._persisted) is not _PersistedConstraints
            or type(self._opening) is not _PersistedConstraints
            or type(self._defaults) is not _TemplateDefaults
            or self._persisted.constraints != self.constraints
            or self._persisted.requested_output_names
            != tuple(value.filename for value in self.output_names)
            or self.changed != (self._persisted != self._opening)
        ):
            reject_constraint_editor(
                ConstraintEditorErrorCode.MODEL_INVALID,
                "constraints.state",
            )

    @property
    def editor_id(self) -> UUID:
        """Compatibility alias for UI coordinators using the shorter token name."""

        return self.editor_session_id

    def replace(self, draft: ConstraintDraftInput) -> ConstraintEditorState:
        """Validate a complete draft atomically and return a new active editor state."""

        if type(draft) is not ConstraintDraftInput:
            reject_constraint_editor(
                ConstraintEditorErrorCode.MODEL_INVALID,
                "constraints.replace",
            )
        constraints = _validated_constraints(draft)
        filename_mode = _select_filename_mode(
            constraints.allowed_modes,
            self.filename_mode,
            self._defaults.filename_mode,
        )
        rows = _resolve_filename_rows(draft.requested_filename_stems, filename_mode)
        if (
            constraints.maximum_file_count is not None
            and len(rows) > constraints.maximum_file_count
        ):
            reject_constraint_editor(
                ConstraintEditorErrorCode.DRAFT_INVALID,
                "constraints.name_count",
                ConstraintValidationField.MAXIMUM_FILE_COUNT,
            )
        persisted = _PersistedConstraints(
            constraints,
            tuple(value.filename for value in rows),
        )
        source = (
            self.source
            if persisted == self._persisted
            else _source_after_edit(self.source, self._defaults.template_title)
        )
        return _editor_state(
            case_id=self.case_id,
            manifest_revision=self.manifest_revision,
            editor_session_id=self.editor_session_id,
            source=source,
            persisted=persisted,
            filename_mode=filename_mode,
            rows=rows,
            opening=self._opening,
            defaults=self._defaults,
            opening_marker=self._opening_marker,
            announcement=ConstraintAnnouncement.DRAFT_VALIDATED,
        )

    def restore_generic_defaults(self) -> ConstraintEditorState:
        """Copy reviewed generic defaults into this local draft without saving them."""

        source = _source_view(
            ConstraintSourceKind.GENERIC_DEFAULTS_DRAFT,
            self._defaults.template_title,
        )
        rows = _resolve_filename_rows(
            self._defaults.filename_stems,
            self._defaults.filename_mode,
        )
        return _editor_state(
            case_id=self.case_id,
            manifest_revision=self.manifest_revision,
            editor_session_id=self.editor_session_id,
            source=source,
            persisted=self._defaults.persisted,
            filename_mode=self._defaults.filename_mode,
            rows=rows,
            opening=self._opening,
            defaults=self._defaults,
            opening_marker=self._opening_marker,
            announcement=ConstraintAnnouncement.GENERIC_DEFAULTS_RESTORED,
        )

    def matches_opening(self, opening: ConstraintEditorState) -> bool:
        """Return whether both states belong to the exact private opening authority."""

        return (
            type(opening) is ConstraintEditorState
            and self.case_id == opening.case_id
            and self.manifest_revision == opening.manifest_revision
            and self.editor_session_id == opening.editor_session_id
            and self._opening_marker is opening._opening_marker
            and self._opening is opening._opening
        )

    def __repr__(self) -> str:
        return (
            "ConstraintEditorState("
            f"case_id={self.case_id!r}, manifest_revision={self.manifest_revision!r}, "
            f"editor_session_id={self.editor_session_id!r}, "
            f"source={self.source.kind.value!r}, "
            f"allowed_mode_count={len(self.constraints.allowed_modes)}, "
            f"requested_name_count={len(self.output_names)}, changed={self.changed!r}, "
            f"announcement={self.announcement.name!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class _OpeningAuthority:
    state: ConstraintEditorState = dataclass_field(repr=False)
    snapshot: ManifestSnapshot = dataclass_field(repr=False)


class PortalConstraintWorkflowService:
    """Own one exact opening authority and one reviewed generic profile."""

    __slots__ = (
        "_active",
        "_as_of",
        "_id_factory",
        "_loader",
        "_template_key",
    )

    def __init__(
        self,
        loader: SafeTemplateLoader,
        template_key: TemplateKey,
        *,
        as_of: date,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if (
            not isinstance(loader, SafeTemplateLoader)
            or type(template_key) is not TemplateKey
            or type(as_of) is not date
            or not callable(id_factory)
        ):
            reject_constraint_editor(
                ConstraintEditorErrorCode.MODEL_INVALID,
                "constraints.workflow",
            )
        self._loader = loader
        self._template_key = template_key
        self._as_of = as_of
        self._id_factory = id_factory
        self._active: _OpeningAuthority | None = None

    def begin(self, snapshot: ManifestSnapshot) -> ConstraintEditorState:
        """Load one exact manifest revision into a detached, path-free editor state."""

        if (
            type(snapshot) is not ManifestSnapshot
            or snapshot.schema_version != CURRENT_MANIFEST_SCHEMA_VERSION
        ):
            reject_constraint_editor(
                ConstraintEditorErrorCode.MANIFEST_INVALID,
                "constraints.begin_snapshot",
            )
        if self._active is not None:
            reject_constraint_editor(
                ConstraintEditorErrorCode.WORKFLOW_ACTIVE,
                "constraints.begin_active",
            )
        profile, template_title = self._load_profile()
        opening = _persisted_from_manifest(snapshot)
        defaults = _template_defaults(profile, template_title)
        rows, filename_mode = _rows_from_persisted(
            opening,
            preferred_mode=profile.constraints.default_mode,
        )
        source_kind = _opening_source_kind(snapshot, profile, opening, defaults.persisted)
        editor_session_id = _new_editor_id(self._id_factory, {snapshot.case_id})
        marker = object()
        state = _editor_state(
            case_id=snapshot.case_id,
            manifest_revision=snapshot.revision,
            editor_session_id=editor_session_id,
            source=_source_view(source_kind, template_title),
            persisted=opening,
            filename_mode=filename_mode,
            rows=rows,
            opening=opening,
            defaults=defaults,
            opening_marker=marker,
            announcement=ConstraintAnnouncement.READY,
        )
        self._active = _OpeningAuthority(state, snapshot)
        return state

    def to_candidate(
        self,
        opening_state: ConstraintEditorState,
        editor_state: ConstraintEditorState,
        current_snapshot: ManifestSnapshot,
    ) -> dict[str, object]:
        """Reconstruct one candidate while leaving revision, time, and CAS to the organizer."""

        authority = self._require_active(opening_state, editor_state)
        if (
            type(current_snapshot) is not ManifestSnapshot
            or current_snapshot.case_id != authority.snapshot.case_id
            or current_snapshot.schema_version != CURRENT_MANIFEST_SCHEMA_VERSION
            or current_snapshot.revision != authority.snapshot.revision
            or current_snapshot.sha256 != authority.snapshot.sha256
            or current_snapshot.cas_token != authority.snapshot.cas_token
        ):
            reject_constraint_editor(
                ConstraintEditorErrorCode.SESSION_MISMATCH,
                "constraints.candidate_snapshot",
            )
        candidate = current_snapshot.document
        candidate["constraints"] = editor_state._persisted.to_document()
        return candidate

    def complete(
        self,
        opening_state: ConstraintEditorState,
        editor_state: ConstraintEditorState,
    ) -> None:
        """Release authority only after durable save or an accepted no-change result."""

        self._require_active(opening_state, editor_state)
        self._active = None

    def cancel(
        self,
        opening_state: ConstraintEditorState,
        editor_state: ConstraintEditorState,
    ) -> None:
        """Discard one matching local draft before an Apply request is accepted."""

        self._require_active(opening_state, editor_state)
        self._active = None

    def abort(self, editor_session_id: UUID) -> None:
        """Release a session whose UI could not be presented or retained."""

        active = self._active
        if (
            active is None
            or not _is_uuid(editor_session_id)
            or active.state.editor_session_id != editor_session_id
        ):
            reject_constraint_editor(
                ConstraintEditorErrorCode.SESSION_MISMATCH,
                "constraints.abort",
            )
        self._active = None

    def _load_profile(self) -> tuple[PortalConstraintProfile, str]:
        template_failure = False
        raw_template: TemplateSnapshot | None = None
        profile: PortalConstraintProfile | None = None
        template_title: str | None = None
        try:
            raw_template = self._loader.load(self._template_key, as_of=self._as_of)
            profile = portal_constraint_profile_from_template(raw_template)
            template_title = raw_template.presentation.title
            if type(template_title) is not str or not template_title:
                raise TypeError
        except TemplateLoadError, PreflightError:
            template_failure = True
        except Exception:
            template_failure = True
        if template_failure or raw_template is None or profile is None or template_title is None:
            reject_constraint_editor(
                ConstraintEditorErrorCode.TEMPLATE_UNAVAILABLE,
                "constraints.begin_template",
            )
        # TemplateSnapshot is already checked by the profile converter.  Keep its
        # human title out of reprs while making it available to the visible source panel.
        return profile, template_title

    def _require_active(
        self,
        opening_state: ConstraintEditorState,
        editor_state: ConstraintEditorState,
    ) -> _OpeningAuthority:
        active = self._active
        if (
            type(opening_state) is not ConstraintEditorState
            or type(editor_state) is not ConstraintEditorState
            or active is None
            or opening_state is not active.state
            or not editor_state.matches_opening(opening_state)
            or editor_state.manifest_revision != active.snapshot.revision
        ):
            reject_constraint_editor(
                ConstraintEditorErrorCode.SESSION_MISMATCH,
                "constraints.active",
            )
        return active


def preview_output_names(
    stems: tuple[str, ...],
    mode: PortalOutputMode,
) -> tuple[ConstraintFilenameView, ...]:
    """Validate local name stems and return safe resolved component previews."""

    if (
        type(stems) is not tuple
        or any(type(value) is not str for value in stems)
        or type(mode) is not PortalOutputMode
    ):
        reject_constraint_editor(
            ConstraintEditorErrorCode.DRAFT_INVALID,
            "constraints.preview_names",
            ConstraintValidationField.OUTPUT_NAMES,
        )
    return _resolve_filename_rows(stems, mode)


def is_constraint_announcement_safe(value: object) -> bool:
    """Return whether a value belongs to the closed announcement registry."""

    return type(value) is ConstraintAnnouncement


def _validated_constraints(draft: ConstraintDraftInput) -> PortalConstraints:
    modes = draft.allowed_modes
    if not 1 <= len(modes) <= len(PortalOutputMode) or len(set(modes)) != len(modes):
        reject_constraint_editor(
            ConstraintEditorErrorCode.DRAFT_INVALID,
            "constraints.allowed_modes",
            ConstraintValidationField.ALLOWED_MODES,
        )
    limits = (
        (
            draft.maximum_file_count,
            MAX_SAFE_MANIFEST_INTEGER,
            ConstraintValidationField.MAXIMUM_FILE_COUNT,
        ),
        (
            draft.maximum_total_bytes,
            MAX_SAFE_MANIFEST_INTEGER,
            ConstraintValidationField.MAXIMUM_TOTAL_BYTES,
        ),
        (
            draft.maximum_file_bytes,
            MAX_SAFE_MANIFEST_INTEGER,
            ConstraintValidationField.MAXIMUM_FILE_BYTES,
        ),
        (
            draft.maximum_pages_per_file,
            MAX_SAFE_MANIFEST_INTEGER,
            ConstraintValidationField.MAXIMUM_PAGES_PER_FILE,
        ),
    )
    for value, maximum, selected_field in limits:
        if value is not None and (type(value) is not int or not 1 <= value <= maximum):
            reject_constraint_editor(
                ConstraintEditorErrorCode.DRAFT_INVALID,
                "constraints.limit",
                selected_field,
            )
    try:
        return PortalConstraints(
            allowed_modes=modes,
            encryption_policy=draft.encryption_policy,
            maximum_file_count=draft.maximum_file_count,
            maximum_total_bytes=draft.maximum_total_bytes,
            maximum_file_bytes=draft.maximum_file_bytes,
            maximum_pages_per_file=draft.maximum_pages_per_file,
            default_mode=None,
        )
    except PreflightError:
        reject_constraint_editor(
            ConstraintEditorErrorCode.DRAFT_INVALID,
            "constraints.domain",
        )


def _resolve_filename_rows(
    stems: tuple[str, ...],
    mode: PortalOutputMode,
) -> tuple[ConstraintFilenameView, ...]:
    if not stems:
        return ()
    if len(stems) > MAX_PORTAL_OUTPUTS:
        reject_constraint_editor(
            ConstraintEditorErrorCode.DRAFT_INVALID,
            "constraints.output_names",
            ConstraintValidationField.OUTPUT_NAMES,
        )
    if any(value.casefold().endswith((".pdf", ".zip")) for value in stems):
        reject_constraint_editor(
            ConstraintEditorErrorCode.DRAFT_INVALID,
            "constraints.output_extensions",
            ConstraintValidationField.OUTPUT_NAMES,
        )
    try:
        requests = tuple(
            PortalFilenameRequest(f"output-{position}", position, stem)
            for position, stem in enumerate(stems, start=1)
        )
        resolved: tuple[ResolvedOutputFilename, ...]
        if mode is PortalOutputMode.ZIP:
            if len(requests) != 1:
                reject_constraint_editor(
                    ConstraintEditorErrorCode.DRAFT_INVALID,
                    "constraints.archive_name_count",
                    ConstraintValidationField.OUTPUT_NAMES,
                )
            resolved = (resolve_zip_archive_filename(requests[0]),)
        else:
            resolved = resolve_output_filenames(requests, mode)
    except PreflightError:
        reject_constraint_editor(
            ConstraintEditorErrorCode.DRAFT_INVALID,
            "constraints.output_names",
            ConstraintValidationField.OUTPUT_NAMES,
        )
    extension = ".zip" if mode is PortalOutputMode.ZIP else ".pdf"
    return tuple(
        ConstraintFilenameView(
            request.output_key,
            request.position,
            request.stem,
            extension,
            selected.filename,
            selected.collision_index,
        )
        for request, selected in zip(requests, resolved, strict=True)
    )


def _persisted_from_manifest(snapshot: ManifestSnapshot) -> _PersistedConstraints:
    document = snapshot.document
    raw = document.get("constraints")
    if type(raw) is not dict:
        reject_constraint_editor(
            ConstraintEditorErrorCode.MANIFEST_INVALID,
            "constraints.manifest",
        )
    values = cast(dict[str, object], raw)
    try:
        raw_modes = values["allowed_output_modes"]
        if type(raw_modes) is not list:
            raise TypeError
        modes = tuple(PortalOutputMode(value) for value in raw_modes)
        raw_encryption = values["encryption_policy"]
        if type(raw_encryption) is not str:
            raise TypeError
        encryption = PortalEncryptionPolicy(raw_encryption)
        names_value = values.get("requested_output_names", [])
        if type(names_value) is not list or any(type(value) is not str for value in names_value):
            raise TypeError
        names = tuple(cast(list[str], names_value))
        draft = ConstraintDraftInput(
            modes,
            encryption,
            _optional_int(values.get("maximum_file_count")),
            _optional_int(values.get("maximum_total_bytes")),
            _optional_int(values.get("maximum_file_bytes")),
            _optional_int(values.get("maximum_pages_per_file")),
            (),
        )
        constraints = _validated_constraints(draft)
    except KeyError, TypeError, ValueError, ConstraintEditorError:
        reject_constraint_editor(
            ConstraintEditorErrorCode.MANIFEST_INVALID,
            "constraints.manifest_values",
        )
    if values.get("constraints_version") != constraints.constraints_version:
        reject_constraint_editor(
            ConstraintEditorErrorCode.MANIFEST_INVALID,
            "constraints.manifest_version",
        )
    return _PersistedConstraints(constraints, names)


def _rows_from_persisted(
    persisted: _PersistedConstraints,
    *,
    preferred_mode: PortalOutputMode | None,
) -> tuple[tuple[ConstraintFilenameView, ...], PortalOutputMode]:
    names = persisted.requested_output_names
    if not names:
        mode = _select_filename_mode(persisted.constraints.allowed_modes, preferred_mode, None)
        return (), mode
    folded = tuple(value.casefold() for value in names)
    if all(value.endswith(".zip") for value in folded):
        mode = PortalOutputMode.ZIP
        suffix_length = 4
    elif all(value.endswith(".pdf") for value in folded):
        pdf_modes = tuple(
            value
            for value in persisted.constraints.allowed_modes
            if value is not PortalOutputMode.ZIP
        )
        if not pdf_modes:
            reject_constraint_editor(
                ConstraintEditorErrorCode.MANIFEST_INVALID,
                "constraints.manifest_mode",
            )
        mode = _select_filename_mode(pdf_modes, preferred_mode, None)
        suffix_length = 4
    else:
        reject_constraint_editor(
            ConstraintEditorErrorCode.MANIFEST_INVALID,
            "constraints.manifest_extensions",
        )
    if mode not in persisted.constraints.allowed_modes:
        reject_constraint_editor(
            ConstraintEditorErrorCode.MANIFEST_INVALID,
            "constraints.manifest_mode",
        )
    stems = tuple(value[:-suffix_length] for value in names)
    try:
        rows = _resolve_filename_rows(stems, mode)
    except ConstraintEditorError:
        reject_constraint_editor(
            ConstraintEditorErrorCode.MANIFEST_INVALID,
            "constraints.manifest_names",
        )
    if tuple(value.filename for value in rows) != names:
        reject_constraint_editor(
            ConstraintEditorErrorCode.MANIFEST_INVALID,
            "constraints.manifest_names",
        )
    if (
        persisted.constraints.maximum_file_count is not None
        and len(rows) > persisted.constraints.maximum_file_count
    ):
        reject_constraint_editor(
            ConstraintEditorErrorCode.MANIFEST_INVALID,
            "constraints.manifest_name_count",
        )
    return rows, mode


def _template_defaults(
    profile: PortalConstraintProfile,
    template_title: str,
) -> _TemplateDefaults:
    source = profile.constraints
    mode = source.default_mode
    if mode is None:
        reject_constraint_editor(
            ConstraintEditorErrorCode.TEMPLATE_UNAVAILABLE,
            "constraints.template_default_mode",
        )
    constraints = PortalConstraints(
        allowed_modes=source.allowed_modes,
        encryption_policy=source.encryption_policy,
        maximum_file_count=source.maximum_file_count,
        maximum_total_bytes=source.maximum_total_bytes,
        maximum_file_bytes=source.maximum_file_bytes,
        maximum_pages_per_file=source.maximum_pages_per_file,
        default_mode=None,
    )
    stems = (
        tuple(value.stem for value in profile.filename_policy.requirement_stems)
        if mode is PortalOutputMode.SEPARATE_PDFS
        else (profile.filename_policy.package_stem,)
    )
    rows = _resolve_filename_rows(stems, mode)
    persisted = _PersistedConstraints(constraints, tuple(value.filename for value in rows))
    return _TemplateDefaults(persisted, mode, stems, template_title)


def _select_filename_mode(
    allowed_modes: tuple[PortalOutputMode, ...],
    preferred: PortalOutputMode | None,
    fallback: PortalOutputMode | None,
) -> PortalOutputMode:
    if preferred is not None and preferred in allowed_modes:
        return preferred
    if fallback is not None and fallback in allowed_modes:
        return fallback
    if allowed_modes:
        return allowed_modes[0]
    reject_constraint_editor(
        ConstraintEditorErrorCode.DRAFT_INVALID,
        "constraints.filename_mode",
        ConstraintValidationField.ALLOWED_MODES,
    )


def _editor_state(
    *,
    case_id: UUID,
    manifest_revision: int,
    editor_session_id: UUID,
    source: ConstraintSourceView,
    persisted: _PersistedConstraints,
    filename_mode: PortalOutputMode,
    rows: tuple[ConstraintFilenameView, ...],
    opening: _PersistedConstraints,
    defaults: _TemplateDefaults,
    opening_marker: object,
    announcement: ConstraintAnnouncement,
) -> ConstraintEditorState:
    return ConstraintEditorState(
        case_id,
        manifest_revision,
        editor_session_id,
        source,
        persisted.constraints,
        filename_mode,
        rows,
        persisted != opening,
        announcement,
        persisted,
        opening,
        defaults,
        opening_marker,
    )


def _source_view(kind: ConstraintSourceKind, template_title: str) -> ConstraintSourceView:
    values = {
        ConstraintSourceKind.GENERIC_TEMPLATE: (
            "Generic template defaults",
            "These values match the exact configured generic template. They are packaging defaults, "
            "not evidence that a portal will accept the files.",
        ),
        ConstraintSourceKind.CASE_OVERRIDE: (
            "Case-specific override",
            "This case differs from the configured generic defaults. Review every active value "
            "against the current application instructions.",
        ),
        ConstraintSourceKind.MANUAL: (
            "Manual case constraints",
            "This case is not bound to the configured generic template. The values are stored "
            "specifically for this case and still require comparison with current portal "
            "instructions.",
        ),
        ConstraintSourceKind.GENERIC_DEFAULTS_DRAFT: (
            "Generic defaults copied into this draft",
            "These configured example defaults have not been saved yet. Review them against "
            "current portal instructions before Apply.",
        ),
    }
    heading, detail = values[kind]
    return ConstraintSourceView(kind, heading, detail, template_title, True)


def _source_after_edit(
    source: ConstraintSourceView,
    template_title: str,
) -> ConstraintSourceView:
    if source.kind is ConstraintSourceKind.MANUAL:
        return _source_view(ConstraintSourceKind.MANUAL, template_title)
    if source.kind is ConstraintSourceKind.GENERIC_DEFAULTS_DRAFT:
        return _source_view(ConstraintSourceKind.GENERIC_DEFAULTS_DRAFT, template_title)
    return _source_view(ConstraintSourceKind.CASE_OVERRIDE, template_title)


def _opening_source_kind(
    snapshot: ManifestSnapshot,
    profile: PortalConstraintProfile,
    opening: _PersistedConstraints,
    defaults: _PersistedConstraints,
) -> ConstraintSourceKind:
    reference = snapshot.document.get("template_ref")
    if reference is None:
        return ConstraintSourceKind.MANUAL
    if type(reference) is dict:
        selected = cast(dict[str, object], reference)
        binding = profile.binding
        if (
            selected.get("template_id") == binding.template_id
            and selected.get("template_version") == binding.template_version
            and selected.get("sha256") == binding.sha256
        ):
            return (
                ConstraintSourceKind.GENERIC_TEMPLATE
                if opening == defaults
                else ConstraintSourceKind.CASE_OVERRIDE
            )
    return ConstraintSourceKind.CASE_OVERRIDE


def _new_editor_id(factory: Callable[[], UUID], reserved: set[UUID]) -> UUID:
    for _ in range(_MAX_EDITOR_ID_ATTEMPTS):
        failed = False
        value: object = None
        try:
            value = factory()
        except BaseException:
            failed = True
        if not failed and _is_uuid(value) and value not in reserved:
            return cast(UUID, value)
    reject_constraint_editor(
        ConstraintEditorErrorCode.ID_GENERATION_FAILED,
        "constraints.editor_id",
    )


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise TypeError
    return value


def _is_uuid(value: object) -> bool:
    return type(value) is UUID and value.version == 4


__all__ = (
    "ConstraintAnnouncement",
    "ConstraintDraftInput",
    "ConstraintEditorError",
    "ConstraintEditorErrorCode",
    "ConstraintEditorState",
    "ConstraintFilenameView",
    "ConstraintSourceKind",
    "ConstraintSourceView",
    "ConstraintValidationField",
    "PortalConstraintWorkflowService",
    "is_constraint_announcement_safe",
    "preview_output_names",
    "reject_constraint_editor",
)

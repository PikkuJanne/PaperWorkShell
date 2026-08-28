from __future__ import annotations

import traceback
from collections.abc import Callable
from copy import deepcopy
from datetime import date
from uuid import UUID

import pytest

from paperworkshell.app.portal_constraints import (
    ConstraintAnnouncement,
    ConstraintDraftInput,
    ConstraintEditorError,
    ConstraintEditorErrorCode,
    ConstraintSourceKind,
    ConstraintValidationField,
    PortalConstraintWorkflowService,
    is_constraint_announcement_safe,
    preview_output_names,
)
from paperworkshell.domain.manifest_repository import (
    MAX_SAFE_MANIFEST_INTEGER,
    ManifestSnapshot,
)
from paperworkshell.preflight.portal_constraints import (
    PortalEncryptionPolicy,
    PortalOutputMode,
)
from paperworkshell.templates.catalog import (
    GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
    built_in_template_repository,
)
from paperworkshell.templates.loader import SafeTemplateLoader

CASE_ID = UUID("71000000-0000-4000-8000-000000000001")
EDITOR_ID = UUID("72000000-0000-4000-8000-000000000001")
OTHER_EDITOR_ID = UUID("72000000-0000-4000-8000-000000000002")
PRIVATE_CANARY = "private-original-name-and-path-canary"


def _id(value: UUID) -> Callable[[], UUID]:
    return lambda: value


def _document(
    *,
    revision: int = 7,
    constraints: dict[str, object] | None = None,
    template_ref: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 2,
        "case_id": str(CASE_ID),
        "revision": revision,
        "created_at": "2026-08-28T10:00:00Z",
        "updated_at": "2026-08-28T10:01:00Z",
        "template_ref": template_ref,
        "sources": [
            {
                "source_id": "73000000-0000-4000-8000-000000000001",
                "display_name": PRIVATE_CANARY,
                "storage_name": f"{PRIVATE_CANARY}.bin",
            }
        ],
        "pages": [],
        "page_sequence_base": [],
        "page_sequence": [],
        "page_operations": [],
        "assets": [],
        "overlays": [],
        "assignments": [],
        "constraints": constraints
        if constraints is not None
        else {
            "constraints_version": 1,
            "allowed_output_modes": ["combined_pdf"],
            "encryption_policy": "must_be_unencrypted",
        },
        "confirmations": [],
        "export_history": {"storage_name": "export-history.json", "entries": []},
    }


def _snapshot(
    document: dict[str, object] | None = None,
    *,
    sha256: str = "a" * 64,
    cas_token: str = "b" * 64,
) -> ManifestSnapshot:
    selected = _document() if document is None else document
    revision = selected["revision"]
    assert type(revision) is int
    return ManifestSnapshot(
        CASE_ID,
        2,
        revision,
        selected,
        sha256,
        cas_token,
    )


def _service(editor_id: UUID = EDITOR_ID) -> PortalConstraintWorkflowService:
    return PortalConstraintWorkflowService(
        SafeTemplateLoader(built_in_template_repository()),
        GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
        as_of=date(2026, 8, 28),
        id_factory=_id(editor_id),
    )


def _draft(
    *,
    modes: tuple[PortalOutputMode, ...] = (PortalOutputMode.COMBINED_PDF,),
    maximum_file_count: int | None = None,
    maximum_total_bytes: int | None = None,
    maximum_file_bytes: int | None = None,
    maximum_pages_per_file: int | None = None,
    stems: tuple[str, ...] = (),
) -> ConstraintDraftInput:
    return ConstraintDraftInput(
        modes,
        PortalEncryptionPolicy.MUST_BE_UNENCRYPTED,
        maximum_file_count,
        maximum_total_bytes,
        maximum_file_bytes,
        maximum_pages_per_file,
        stems,
    )


def test_manual_draft_is_path_free_and_candidate_changes_only_released_subset() -> None:
    snapshot = _snapshot()
    service = _service()
    opening = service.begin(snapshot)

    assert opening.source.kind is ConstraintSourceKind.MANUAL
    assert opening.case_id == CASE_ID
    assert opening.manifest_revision == snapshot.revision
    assert opening.editor_session_id == EDITOR_ID
    assert not opening.changed
    assert opening.output_names == ()
    assert is_constraint_announcement_safe(opening.announcement)
    assert not is_constraint_announcement_safe(str(opening.announcement))

    edited = opening.replace(
        _draft(
            modes=(PortalOutputMode.ZIP,),
            maximum_file_count=1,
            maximum_total_bytes=1,
            maximum_file_bytes=2,
            maximum_pages_per_file=MAX_SAFE_MANIFEST_INTEGER,
            stems=("packet",),
        )
    )
    assert edited.changed
    assert edited.filename_mode is PortalOutputMode.ZIP
    assert [value.filename for value in edited.output_names] == ["packet.zip"]
    assert all(value.extension == ".zip" for value in edited.output_names)

    candidate = service.to_candidate(opening, edited, snapshot)
    before = snapshot.document
    changed_keys = {key for key in before if before[key] != candidate[key]}
    assert changed_keys == {"constraints"}
    assert candidate["revision"] == before["revision"]
    assert candidate["updated_at"] == before["updated_at"]
    assert candidate["constraints"] == {
        "constraints_version": 1,
        "allowed_output_modes": ["zip"],
        "encryption_policy": "must_be_unencrypted",
        "maximum_file_count": 1,
        "maximum_total_bytes": 1,
        "maximum_file_bytes": 2,
        "maximum_pages_per_file": MAX_SAFE_MANIFEST_INTEGER,
        "requested_output_names": ["packet.zip"],
    }
    serialized = repr(edited)
    assert "packet" not in serialized
    assert PRIVATE_CANARY not in serialized

    service.complete(opening, edited)
    with pytest.raises(ConstraintEditorError) as captured:
        service.to_candidate(opening, edited, snapshot)
    assert captured.value.code is ConstraintEditorErrorCode.SESSION_MISMATCH


def test_full_draft_validation_is_atomic_and_errors_never_echo_hostile_names() -> None:
    service = _service()
    opening = service.begin(_snapshot())

    with pytest.raises(ConstraintEditorError) as no_modes:
        opening.replace(_draft(modes=()))
    assert no_modes.value.field is ConstraintValidationField.ALLOWED_MODES
    assert opening.output_names == ()
    assert not opening.changed

    hostile = f"..\\{PRIVATE_CANARY}"
    hostile_draft = _draft(stems=(hostile,))
    assert PRIVATE_CANARY not in repr(hostile_draft)
    with pytest.raises(ConstraintEditorError) as unsafe_name:
        preview_output_names((hostile,), PortalOutputMode.COMBINED_PDF)
    error = unsafe_name.value
    rendered = "".join(traceback.format_exception(error))
    assert error.field is ConstraintValidationField.OUTPUT_NAMES
    assert PRIVATE_CANARY not in str(error)
    assert PRIVATE_CANARY not in repr(error)
    assert PRIVATE_CANARY not in rendered
    assert error.__cause__ is None
    assert error.__context__ is None

    with pytest.raises(ConstraintEditorError) as supplied_extension:
        preview_output_names(("packet.pdf",), PortalOutputMode.COMBINED_PDF)
    assert supplied_extension.value.field is ConstraintValidationField.OUTPUT_NAMES

    with pytest.raises(ConstraintEditorError) as archive_name_count:
        preview_output_names(("first", "second"), PortalOutputMode.ZIP)
    assert archive_name_count.value.field is ConstraintValidationField.OUTPUT_NAMES

    with pytest.raises(ConstraintEditorError) as name_count:
        opening.replace(_draft(maximum_file_count=1, stems=("one", "two")))
    assert name_count.value.field is ConstraintValidationField.MAXIMUM_FILE_COUNT
    assert not opening.changed


def test_generic_defaults_can_be_copied_without_selecting_future_output_organization() -> None:
    service = _service()
    snapshot = _snapshot()
    opening = service.begin(snapshot)

    restored = opening.restore_generic_defaults()
    assert restored.source.kind is ConstraintSourceKind.GENERIC_DEFAULTS_DRAFT
    assert restored.announcement is ConstraintAnnouncement.GENERIC_DEFAULTS_RESTORED
    assert restored.filename_mode is PortalOutputMode.COMBINED_PDF
    assert PortalOutputMode.COMBINED_PDF in restored.constraints.allowed_modes
    assert PortalOutputMode.SEPARATE_PDFS in restored.constraints.allowed_modes
    assert PortalOutputMode.ZIP in restored.constraints.allowed_modes
    assert [value.filename for value in restored.output_names] == ["generic-application-packet.pdf"]

    candidate = service.to_candidate(opening, restored, snapshot)
    persisted = candidate["constraints"]
    assert isinstance(persisted, dict)
    assert "default_mode" not in persisted
    assert "filename_policy" not in persisted
    assert "flatten_policy" not in persisted
    assert set(persisted) <= {
        "constraints_version",
        "allowed_output_modes",
        "encryption_policy",
        "maximum_file_count",
        "maximum_total_bytes",
        "maximum_file_bytes",
        "maximum_pages_per_file",
        "requested_output_names",
    }

    service.complete(opening, restored)
    loader = SafeTemplateLoader(built_in_template_repository())
    template = loader.load(
        GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
        as_of=date(2026, 8, 28),
    )
    linked_document = deepcopy(candidate)
    linked_document["template_ref"] = {
        "template_id": template.key.template_id,
        "template_version": template.key.template_version,
        "sha256": template.sha256,
    }
    linked_service = PortalConstraintWorkflowService(
        loader,
        GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
        as_of=date(2026, 8, 28),
        id_factory=_id(OTHER_EDITOR_ID),
    )
    linked = linked_service.begin(_snapshot(linked_document, sha256="c" * 64))
    assert linked.source.kind is ConstraintSourceKind.GENERIC_TEMPLATE
    assert not linked.changed
    linked_service.cancel(linked, linked)


def test_exact_snapshot_and_session_correlation_rejects_stale_or_foreign_authority() -> None:
    snapshot = _snapshot()
    service = _service()
    opening = service.begin(snapshot)
    edited = opening.replace(_draft(stems=("packet",)))

    changed_document = deepcopy(snapshot.document)
    stale = _snapshot(changed_document, sha256="c" * 64, cas_token="d" * 64)
    with pytest.raises(ConstraintEditorError) as stale_error:
        service.to_candidate(opening, edited, stale)
    assert stale_error.value.code is ConstraintEditorErrorCode.SESSION_MISMATCH

    foreign_service = _service(OTHER_EDITOR_ID)
    foreign = foreign_service.begin(snapshot)
    with pytest.raises(ConstraintEditorError) as foreign_error:
        service.to_candidate(opening, foreign, snapshot)
    assert foreign_error.value.code is ConstraintEditorErrorCode.SESSION_MISMATCH

    service.cancel(opening, edited)
    foreign_service.abort(foreign.editor_session_id)


def test_existing_optional_limits_preserve_none_and_positive_safe_integer_semantics() -> None:
    constraints = {
        "constraints_version": 1,
        "allowed_output_modes": ["combined_pdf"],
        "encryption_policy": "allowed",
        "maximum_file_count": MAX_SAFE_MANIFEST_INTEGER,
        "maximum_total_bytes": None,
    }
    # Optional keys are omitted in released manifests; remove the illustrative None.
    del constraints["maximum_total_bytes"]
    service = _service()
    opening = service.begin(_snapshot(_document(constraints=constraints)))

    assert opening.constraints.maximum_file_count == MAX_SAFE_MANIFEST_INTEGER
    assert opening.constraints.maximum_total_bytes is None
    assert opening.constraints.maximum_file_bytes is None
    assert opening.constraints.maximum_pages_per_file is None
    service.cancel(opening, opening)

from __future__ import annotations

from datetime import date
from typing import cast
from uuid import UUID

from PySide6.QtCore import Qt

from paperworkshell.app.checklist import ChecklistEditorState, ChecklistWorkflowService
from paperworkshell.domain.checklist import ChecklistError, ChecklistErrorCode
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.templates.catalog import (
    GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
    built_in_template_repository,
)
from paperworkshell.templates.loader import SafeTemplateLoader
from paperworkshell.templates.models import TemplateLoadError, TemplateLoadErrorCode
from paperworkshell.ui.backend import sanitize_ui_failure
from paperworkshell.ui.checklist import (
    CHECKLIST_PAGE_MIME_TYPE,
    ChecklistPageListModel,
)

CASE_ID = UUID("81000000-0000-4000-8000-000000000001")
SOURCE_ID = UUID("82000000-0000-4000-8000-000000000001")
PAGE_ID = UUID("83000000-0000-4000-8000-000000000001")
EDITOR_ID = UUID("84000000-0000-4000-8000-000000000001")
PRIVATE_CANARY = "PRIVATE_CHECKLIST_SOURCE_NAME_CANARY.pdf"
STAMP = "2026-08-28T10:00:00Z"


def _snapshot() -> ManifestSnapshot:
    document: dict[str, object] = {
        "schema_version": 2,
        "case_id": str(CASE_ID),
        "revision": 0,
        "created_at": STAMP,
        "updated_at": STAMP,
        "template_ref": None,
        "sources": [
            {
                "source_id": str(SOURCE_ID),
                "display_name": PRIVATE_CANARY,
                "storage_name": f"{SOURCE_ID}.bin",
                "detected_media_type": "application/pdf",
                "byte_size": 128,
                "page_count": 1,
                "sha256": "a" * 64,
                "signature_state": "none_detected",
                "encryption_state": "unencrypted",
                "import_state": "complete",
                "imported_at": STAMP,
            }
        ],
        "pages": [
            {
                "page_id": str(PAGE_ID),
                "source_id": str(SOURCE_ID),
                "source_page_index": 0,
            }
        ],
        "page_sequence_base": [str(PAGE_ID)],
        "page_sequence": [str(PAGE_ID)],
        "page_operations": [],
        "assets": [],
        "overlays": [],
        "assignments": [],
        "constraints": {
            "constraints_version": 1,
            "allowed_output_modes": ["combined_pdf"],
            "encryption_policy": "must_be_unencrypted",
        },
        "confirmations": [],
        "export_history": {"storage_name": "export-history.json", "entries": []},
    }
    return ManifestSnapshot(
        CASE_ID,
        2,
        0,
        document,
        "b" * 64,
        "checklist_privacy_cas_0001",
    )


def _state() -> ChecklistEditorState:
    workflow = ChecklistWorkflowService(
        SafeTemplateLoader(built_in_template_repository()),
        GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
        as_of=date(2026, 8, 28),
        id_factory=lambda: EDITOR_ID,
    )
    return workflow.begin(_snapshot())


def test_editor_state_and_drag_payload_exclude_source_metadata() -> None:
    state = _state()
    rendered = "\n".join(
        (
            repr(state),
            repr(state.pages),
            repr(state.requirements),
            repr(state.assignments),
        )
    )

    assert PRIVATE_CANARY not in rendered
    assert str(SOURCE_ID) not in rendered
    assert "storage_name" not in rendered

    model = ChecklistPageListModel()
    model.replace(state.pages, state.assignments)
    index = model.index(0, 0)
    mime = model.mimeData([index])
    payload = cast(bytes, mime.data(CHECKLIST_PAGE_MIME_TYPE).data())

    assert payload == str(PAGE_ID).encode("ascii")
    assert PRIVATE_CANARY.encode() not in payload
    assert str(SOURCE_ID).encode() not in payload
    assert model.data(index, int(Qt.ItemDataRole.DisplayRole)) == "Page 1 — Unassigned"


def test_checklist_and_template_failures_are_rebuilt_before_ui_presentation() -> None:
    checklist_error = ChecklistError(
        ChecklistErrorCode.PAGE_UNKNOWN,
        "checklist.command_page",
    )
    checklist_error.safe_message = PRIVATE_CANARY
    template_error = TemplateLoadError(
        TemplateLoadErrorCode.INTEGRITY_MISMATCH,
        "template.sha256",
    )
    template_error.safe_message = PRIVATE_CANARY

    checklist_failure = sanitize_ui_failure(checklist_error, operation_id=401)
    template_failure = sanitize_ui_failure(template_error, operation_id=402)

    assert checklist_failure.code == ChecklistErrorCode.PAGE_UNKNOWN.value
    assert template_failure.code == TemplateLoadErrorCode.INTEGRITY_MISMATCH.value
    assert PRIVATE_CANARY not in checklist_failure.message
    assert PRIVATE_CANARY not in template_failure.message
    assert checklist_failure.operation_id == 401
    assert template_failure.operation_id == 402

"""Small synthetic exact-template packet shared by PWS-0408 tests."""

from __future__ import annotations

from datetime import date
from typing import cast
from uuid import UUID

from paperworkshell.app.checklist import checklist_definition_from_template
from paperworkshell.domain.checklist import ASSIGNMENT_CONFIRMATION_CODE
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.templates.catalog import (
    GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
    built_in_template_repository,
)
from paperworkshell.templates.loader import SafeTemplateLoader

AS_OF = date(2026, 9, 5)
CASE_ID = UUID("10000000-0000-4000-8000-000000000001")
SOURCE_ID = UUID("20000000-0000-4000-8000-000000000001")
PAGE_IDS = tuple(UUID(f"30000000-0000-4000-8000-{index:012d}") for index in range(1, 5))


def pilot_document(*, missing: bool = False, signature: str = "none_detected") -> dict[str, object]:
    template = SafeTemplateLoader(built_in_template_repository()).load(
        GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
        as_of=AS_OF,
    )
    definition = checklist_definition_from_template(template)
    assignments: list[dict[str, object]] = []
    confirmations: list[dict[str, object]] = []
    for index, requirement in enumerate(definition.requirements, start=1):
        if missing and index == 4:
            continue
        assignment = f"70000000-0000-4000-8000-{index:012d}"
        confirmation = f"a0000000-0000-4000-8000-{index:012d}"
        assignments.append(
            {
                "assignment_id": assignment,
                "requirement_id": requirement.requirement_id,
                "page_ids": [str(PAGE_IDS[index - 1])],
                "origin": "user",
                "status": "user_confirmed",
                "confirmation_id": confirmation,
                "assigned_at": "2026-09-05T10:01:00Z",
            }
        )
        confirmations.append(
            {
                "confirmation_id": confirmation,
                "confirmation_version": 1,
                "confirmation_code": ASSIGNMENT_CONFIRMATION_CODE,
                "statement_version": 1,
                "state": "confirmed",
                "actor": "user",
                "scope": {"kind": "assignment", "assignment_id": assignment},
                "recorded_at": "2026-09-05T10:02:00Z",
            }
        )
    return {
        "schema_version": 2,
        "case_id": str(CASE_ID),
        "revision": 7,
        "created_at": "2026-09-05T10:00:00Z",
        "updated_at": "2026-09-05T10:03:00Z",
        "template_ref": definition.binding.to_manifest(),
        "sources": [
            {
                "source_id": str(SOURCE_ID),
                "display_name": "PRIVATE-PILOT-SOURCE.pdf",
                "storage_name": f"{SOURCE_ID}.bin",
                "detected_media_type": "application/pdf",
                "byte_size": 4096,
                "page_count": 4,
                "sha256": "a" * 64,
                "signature_state": signature,
                "encryption_state": "unencrypted",
                "import_state": "complete",
                "imported_at": "2026-09-05T10:00:30Z",
            }
        ],
        "pages": [
            {"page_id": str(page), "source_id": str(SOURCE_ID), "source_page_index": index}
            for index, page in enumerate(PAGE_IDS)
        ],
        "page_sequence_base": [str(page) for page in PAGE_IDS],
        "page_sequence": [str(page) for page in PAGE_IDS],
        "page_operations": [],
        "assets": [],
        "overlays": [],
        "assignments": assignments,
        "confirmations": confirmations,
        "constraints": {
            "constraints_version": 1,
            "allowed_output_modes": ["combined_pdf", "separate_pdfs", "zip"],
            "encryption_policy": "must_be_unencrypted",
            "maximum_file_count": 4,
            "maximum_total_bytes": 10485760,
            "maximum_file_bytes": 5242880,
            "maximum_pages_per_file": 100,
        },
        "export_history": {"storage_name": "export-history.json", "entries": []},
    }


def pilot_snapshot(document: dict[str, object] | None = None) -> ManifestSnapshot:
    selected = pilot_document() if document is None else document
    revision = cast(int, selected["revision"])
    return ManifestSnapshot(
        CASE_ID, 2, revision, selected, f"{revision:064x}", f"{revision + 1:064x}"
    )

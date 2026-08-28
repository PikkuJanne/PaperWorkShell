"""Windows persistence evidence for PWS-0403 checklist assignments."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.app.checklist import ChecklistEditorState, ChecklistWorkflowService
from paperworkshell.app.manifest_repository import ManifestAutosaveService
from paperworkshell.domain.checklist import AssignmentReviewState, ChecklistStatus
from paperworkshell.domain.manifest_repository import ManifestAutosaveStatus, ManifestSnapshot
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.manifest_repository import WindowsManifestRepository
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.workspace import CaseWorkspaceManager
from paperworkshell.templates.catalog import (
    GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
    built_in_template_repository,
)
from paperworkshell.templates.loader import SafeTemplateLoader

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="PWS-0403 persistence evidence targets the supported Windows storage boundary",
)

CASE_ID = UUID("1f040300-0000-4000-8000-000000000001")
SOURCE_ID = UUID("2f040300-0000-4000-8000-000000000001")
PAGE_IDS = (
    UUID("3f040300-0000-4000-8000-000000000001"),
    UUID("3f040300-0000-4000-8000-000000000002"),
)
EDITOR_IDS = (
    UUID("4f040300-0000-4000-8000-000000000001"),
    UUID("4f040300-0000-4000-8000-000000000002"),
)
ASSIGNMENT_IDS = (
    UUID("5f040300-0000-4000-8000-000000000001"),
    UUID("5f040300-0000-4000-8000-000000000002"),
)
CONFIRMATION_IDS = (
    UUID("6f040300-0000-4000-8000-000000000001"),
    UUID("6f040300-0000-4000-8000-000000000002"),
)
CREATED_AT = datetime(2026, 8, 28, 12, tzinfo=UTC)


def _quota() -> WorkspaceQuota:
    return WorkspaceQuota(
        maximum_case_bytes=8_000_000,
        maximum_cache_bytes=1_000_000,
        maximum_temp_bytes=4_000_000,
        maximum_entries=128,
        maximum_depth=16,
    )


def _seed_two_pages(repository: WindowsManifestRepository) -> ManifestSnapshot:
    loaded = repository.load(CASE_ID).snapshot
    candidate = loaded.document
    candidate["revision"] = 1
    candidate["updated_at"] = (CREATED_AT + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    candidate["sources"] = [
        {
            "source_id": str(SOURCE_ID),
            "display_name": "Synthetic two-page form.pdf",
            "storage_name": f"{SOURCE_ID}.bin",
            "detected_media_type": "application/pdf",
            "byte_size": 256,
            "page_count": 2,
            "sha256": "a" * 64,
            "signature_state": "none_detected",
            "encryption_state": "unencrypted",
            "import_state": "complete",
            "imported_at": CREATED_AT.isoformat().replace("+00:00", "Z"),
        }
    ]
    candidate["pages"] = [
        {
            "page_id": str(page_id),
            "source_id": str(SOURCE_ID),
            "source_page_index": index,
        }
        for index, page_id in enumerate(PAGE_IDS)
    ]
    candidate["page_sequence_base"] = [str(page_id) for page_id in PAGE_IDS]
    candidate["page_sequence"] = [str(page_id) for page_id in PAGE_IDS]
    return repository.save(
        CASE_ID,
        candidate,
        expected_revision=loaded.revision,
        expected_sha256=loaded.sha256,
        expected_cas_token=loaded.cas_token,
    ).snapshot


def _workflow(editor_id: UUID, observed_at: datetime) -> ChecklistWorkflowService:
    return ChecklistWorkflowService(
        SafeTemplateLoader(built_in_template_repository()),
        GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
        as_of=date(2026, 8, 28),
        clock=lambda: observed_at,
        id_factory=lambda: editor_id,
    )


def _save(
    repository: WindowsManifestRepository,
    workflow: ChecklistWorkflowService,
    opening: ChecklistEditorState,
    edited: ChecklistEditorState,
    snapshot: ManifestSnapshot,
) -> ManifestSnapshot:
    autosave = ManifestAutosaveService(repository, CASE_ID, utc_now=workflow.utc_now)
    autosave.queue(workflow.to_candidate(opening, edited, snapshot))
    saved = autosave.flush()
    assert saved.status is ManifestAutosaveStatus.SAVED
    assert saved.snapshot is not None
    workflow.complete(opening, edited)
    autosave.close()
    return saved.snapshot


def test_checklist_confirmations_survive_real_save_reopen_and_clock_rollback(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    manager = CaseWorkspaceManager(
        profile_root=profile,
        quota=_quota(),
        id_factory=lambda: CASE_ID,
    )
    WindowsCaseLifecycleStore(manager, clock=lambda: CREATED_AT).create_case().close()
    repository = WindowsManifestRepository(manager)
    seeded = _seed_two_pages(repository)
    workflow = _workflow(EDITOR_IDS[0], CREATED_AT - timedelta(hours=3))
    opening = workflow.begin(seeded)
    added = opening.add_pages(
        "application-form",
        (PAGE_IDS[0],),
        clock=lambda: CREATED_AT + timedelta(hours=1),
        id_factory=lambda: ASSIGNMENT_IDS[0],
    )
    confirmed = added.confirm_assignment(
        ASSIGNMENT_IDS[0],
        clock=lambda: CREATED_AT + timedelta(hours=2),
        id_factory=lambda: CONFIRMATION_IDS[0],
    )

    first_saved = _save(repository, workflow, opening, confirmed, seeded)

    first_assignments = cast(list[dict[str, object]], first_saved.document["assignments"])
    first_confirmations = cast(list[dict[str, object]], first_saved.document["confirmations"])
    assert first_assignments[0]["assigned_at"] == "2026-08-28T13:00:00Z"
    assert first_confirmations[0]["recorded_at"] == "2026-08-28T14:00:00Z"
    assert first_saved.document["updated_at"] == "2026-08-28T14:00:00.000001Z"

    fresh_repository = WindowsManifestRepository(
        CaseWorkspaceManager(profile_root=profile, quota=_quota())
    )
    reopened_snapshot = fresh_repository.load(CASE_ID).snapshot
    reopened_workflow = _workflow(EDITOR_IDS[1], CREATED_AT - timedelta(hours=4))
    reopened = reopened_workflow.begin(reopened_snapshot)
    assert reopened.assignments[0].review_state is AssignmentReviewState.USER_CONFIRMED
    assert reopened.requirements[0].status is ChecklistStatus.READY
    assert not reopened.changed
    assert not reopened.can_undo
    assert not reopened.can_redo

    second_added = reopened.add_pages(
        "identity-evidence",
        (PAGE_IDS[1],),
        id_factory=lambda: ASSIGNMENT_IDS[1],
    )
    second_confirmed = second_added.confirm_assignment(
        ASSIGNMENT_IDS[1],
        id_factory=lambda: CONFIRMATION_IDS[1],
    )
    second_saved = _save(
        fresh_repository,
        reopened_workflow,
        reopened,
        second_confirmed,
        reopened_snapshot,
    )

    assignments = cast(list[dict[str, object]], second_saved.document["assignments"])
    confirmations = cast(list[dict[str, object]], second_saved.document["confirmations"])
    assert assignments[1]["assigned_at"] == "2026-08-28T14:00:00.000002Z"
    assert confirmations[1]["recorded_at"] == "2026-08-28T14:00:00.000003Z"
    assert second_saved.document["updated_at"] == "2026-08-28T14:00:00.000004Z"

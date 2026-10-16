"""Atomic intent persistence and low-storage recovery without document processing."""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.app.manifest_repository import ManifestAutosaveService
from paperworkshell.app.redaction_tools import RedactionToolsState
from paperworkshell.domain.manifest_repository import (
    ManifestRepositoryError,
    ManifestRepositoryErrorCode,
)
from paperworkshell.domain.pdf_export import PdfExportPlan, build_pdf_export_plan
from paperworkshell.domain.redaction import (
    RedactionError,
    RedactionErrorCode,
    RedactionMarkCollection,
)
from paperworkshell.domain.searchable_pdf_export import (
    SearchablePdfExportPlan,
    build_searchable_pdf_export_plan,
)
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.errors import WorkspaceError, WorkspaceErrorCode
from paperworkshell.storage.manifest_repository import WindowsManifestRepository
from paperworkshell.storage.source_ingest import WindowsSourceIngestStore
from paperworkshell.storage.source_precheck import WindowsSourcePrechecker
from paperworkshell.storage.workspace import CaseWorkspaceManager
from tests.integration.test_core_overlay_tools_persistence_windows import (
    CASE_ID,
    CREATED_AT,
    PAGE_ID,
    _quota,
    _seed_one_page,
)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Supported Windows storage boundary")
MARK_ID = UUID("8f060200-0000-4000-8000-000000000001")


def test_intent_save_reopen_low_disk_failure_and_retry_preserve_original_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    manager = CaseWorkspaceManager(profile_root=profile, quota=_quota(), id_factory=lambda: CASE_ID)
    WindowsCaseLifecycleStore(manager, clock=lambda: CREATED_AT).create_case().close()
    repository = WindowsManifestRepository(manager)
    seeded = _seed_one_page(repository)
    original_pdf_plan = build_pdf_export_plan(seeded)
    original_searchable_plan = build_searchable_pdf_export_plan(seeded)
    source_facts = seeded.document["sources"]
    editor = RedactionToolsState.from_snapshot(seeded, PAGE_ID).create(id_factory=lambda: MARK_ID)
    autosave = ManifestAutosaveService(repository, CASE_ID)
    autosave.queue(editor.to_candidate(seeded))
    original_manifest = manager.layout(CASE_ID).manifest.read_bytes()

    def low_disk(*_args: object, **_kwargs: object) -> object:
        raise WorkspaceError(
            WorkspaceErrorCode.QUOTA_EXCEEDED,
            "test.redaction_disk",
            "The case does not have enough configured storage capacity.",
        )

    monkeypatch.setattr(CaseWorkspaceManager, "reserve_capacity", low_disk)
    with pytest.raises(ManifestRepositoryError) as failure:
        autosave.flush()
    assert failure.value.code is ManifestRepositoryErrorCode.CAPACITY_EXCEEDED
    assert autosave.pending
    assert manager.layout(CASE_ID).manifest.read_bytes() == original_manifest
    monkeypatch.undo()

    saved = autosave.flush()
    assert saved.snapshot is not None
    reopened = (
        WindowsManifestRepository(CaseWorkspaceManager(profile_root=profile, quota=_quota()))
        .load(CASE_ID)
        .snapshot
    )
    marks = RedactionMarkCollection.from_manifest_document(reopened.document).marks
    assert len(marks) == 1 and marks[0].mark_id == MARK_ID
    assert marks[0].state.value == "unapplied"
    assert reopened.document["sources"] == source_facts
    assert reopened.document["overlays"] == seeded.document["overlays"]
    assert not tuple(manager.layout(CASE_ID).temp.iterdir())
    assert reopened.revision == seeded.revision + 1
    source_store = WindowsSourceIngestStore(
        manager,
        cast(WindowsSourcePrechecker, object()),
        maximum_page_count=4096,
    )
    direct_pdf_plan = PdfExportPlan(
        case_id=original_pdf_plan.case_id,
        manifest_revision=reopened.revision,
        manifest_sha256=reopened.sha256,
        manifest_cas_token=reopened.cas_token,
        sources=original_pdf_plan.sources,
        pages=original_pdf_plan.pages,
        profile=original_pdf_plan.profile,
        plan_version=original_pdf_plan.plan_version,
    )
    direct_searchable_plan = SearchablePdfExportPlan(
        case_id=original_searchable_plan.case_id,
        manifest_revision=reopened.revision,
        manifest_sha256=reopened.sha256,
        manifest_cas_token=reopened.cas_token,
        sources=original_searchable_plan.sources,
        pages=original_searchable_plan.pages,
        profile=original_searchable_plan.profile,
        plan_version=original_searchable_plan.plan_version,
    )
    with pytest.raises(RedactionError) as pdf_failure:
        source_store.borrow_committed_pdf_sources(
            direct_pdf_plan,
            deadline=lambda: False,
            cancellation=lambda: False,
        )
    with pytest.raises(RedactionError) as searchable_failure:
        source_store.borrow_committed_searchable_pdf_sources(
            direct_searchable_plan,
            deadline=lambda: False,
            cancellation=lambda: False,
        )
    assert pdf_failure.value.code is RedactionErrorCode.PIPELINE_DISABLED
    assert searchable_failure.value.code is RedactionErrorCode.PIPELINE_DISABLED
    autosave.close()

"""Windows persistence evidence for PWS-0304 overlay command candidates."""

from __future__ import annotations

import copy
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.app.manifest_repository import ManifestAutosaveService
from paperworkshell.domain.manifest_repository import ManifestAutosaveStatus, ManifestSnapshot
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.overlays.commands import CreateCoreOverlayCommand, OverlayCommandHistory
from paperworkshell.overlays.models import OverlayKind
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.manifest_repository import WindowsManifestRepository
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.workspace import CaseWorkspaceManager

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="PWS-0304 persistence evidence targets the supported Windows storage boundary",
)

CASE_ID = UUID("1f040000-0000-4000-8000-000000000001")
SOURCE_ID = UUID("2f040000-0000-4000-8000-000000000001")
PAGE_ID = UUID("3f040000-0000-4000-8000-000000000001")
OVERLAY_ID = UUID("6f040000-0000-4000-8000-000000000001")
CREATED_AT = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)


def _quota() -> WorkspaceQuota:
    return WorkspaceQuota(
        maximum_case_bytes=8_000_000,
        maximum_cache_bytes=1_000_000,
        maximum_temp_bytes=4_000_000,
        maximum_entries=128,
        maximum_depth=16,
    )


def _seed_one_page(repository: WindowsManifestRepository) -> ManifestSnapshot:
    loaded = repository.load(CASE_ID).snapshot
    candidate = loaded.document
    candidate["revision"] = 1
    candidate["updated_at"] = (
        (CREATED_AT + timedelta(seconds=1))
        .isoformat()
        .replace(
            "+00:00",
            "Z",
        )
    )
    candidate["sources"] = [
        {
            "source_id": str(SOURCE_ID),
            "display_name": "Synthetic form.pdf",
            "storage_name": f"{SOURCE_ID}.bin",
            "detected_media_type": "application/pdf",
            "byte_size": 128,
            "page_count": 1,
            "sha256": "a" * 64,
            "signature_state": "none_detected",
            "encryption_state": "unencrypted",
            "import_state": "complete",
            "imported_at": CREATED_AT.isoformat().replace("+00:00", "Z"),
        }
    ]
    candidate["pages"] = [
        {
            "page_id": str(PAGE_ID),
            "source_id": str(SOURCE_ID),
            "source_page_index": 0,
        }
    ]
    candidate["page_sequence_base"] = [str(PAGE_ID)]
    candidate["page_sequence"] = [str(PAGE_ID)]
    return repository.save(
        CASE_ID,
        candidate,
        expected_revision=loaded.revision,
        expected_sha256=loaded.sha256,
        expected_cas_token=loaded.cas_token,
    ).snapshot


def test_overlay_date_autosave_reopen_undo_and_redo_preserve_exact_wire(
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
    seeded = _seed_one_page(repository)
    history = OverlayCommandHistory.from_snapshot(seeded).execute(
        CreateCoreOverlayCommand(
            OverlayKind.DATE,
            PAGE_ID,
            NormalizedPageBox(100_000, 200_000, 250_000, 80_000),
            " 24.VIII.2036 / user format \n",
        ),
        id_factory=lambda: OVERLAY_ID,
    )
    source_before = copy.deepcopy(seeded.document["sources"])
    autosave = ManifestAutosaveService(repository, CASE_ID)
    autosave.queue(history.to_candidate(seeded))

    created = autosave.flush()

    assert created.status is ManifestAutosaveStatus.SAVED
    assert created.snapshot is not None
    fresh_manager = CaseWorkspaceManager(profile_root=profile, quota=_quota())
    reopened = WindowsManifestRepository(fresh_manager).load(CASE_ID).snapshot
    overlays = cast(list[dict[str, object]], reopened.document["overlays"])
    assert len(overlays) == 1
    payload = cast(dict[str, object], overlays[0]["payload"])
    assert payload["value"] == " 24.VIII.2036 / user format \n"
    assert reopened.document["sources"] == source_before

    undone = history.undo()
    autosave.queue(undone.to_candidate(reopened))
    removed = autosave.flush()
    assert removed.snapshot is not None
    assert removed.snapshot.document["overlays"] == []

    redone = undone.redo()
    autosave.queue(redone.to_candidate(removed.snapshot))
    restored = autosave.flush()
    assert restored.snapshot is not None
    assert restored.snapshot.document["overlays"] == overlays
    assert restored.snapshot.document["sources"] == source_before

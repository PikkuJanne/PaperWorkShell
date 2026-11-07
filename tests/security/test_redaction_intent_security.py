"""Fail-closed intent export and content-free error boundaries."""

from __future__ import annotations

import logging
from contextlib import nullcontext
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest

import paperworkshell.ui.backend as backend_module
from paperworkshell.app.redaction_tools import RedactionToolsState
from paperworkshell.domain.manifest_repository import (
    CURRENT_MANIFEST_SCHEMA_VERSION,
    ManifestLoadResult,
    ManifestLoadStatus,
    ManifestSnapshot,
)
from paperworkshell.domain.pdf_export import PdfExportPlan, build_pdf_export_plan
from paperworkshell.domain.redaction import RedactionError, RedactionErrorCode
from paperworkshell.domain.searchable_pdf_export import (
    SearchablePdfExportPlan,
    build_searchable_pdf_export_plan,
)
from paperworkshell.storage import source_ingest as ingest_storage
from paperworkshell.storage.source_precheck import WindowsSourcePrechecker
from paperworkshell.storage.workspace import CaseWorkspaceManager
from paperworkshell.ui.backend import sanitize_ui_failure
from tests.unit.test_pdf_export_service import CASE_ID, PAGE_ID, _export, _stack

MARK_ID = UUID("8f060200-0000-4000-8000-000000000101")


def _current_snapshot(snapshot: ManifestSnapshot) -> ManifestSnapshot:
    document = snapshot.document
    document["schema_version"] = CURRENT_MANIFEST_SCHEMA_VERSION
    document["redaction_marks"] = []
    sources = cast(list[dict[str, object]], document["sources"])
    sources[0]["display_name"] = "PRIVATE_SOURCE_NAME_CANARY.pdf"
    return ManifestSnapshot(
        snapshot.case_id,
        CURRENT_MANIFEST_SCHEMA_VERSION,
        snapshot.revision,
        document,
        snapshot.sha256,
        snapshot.cas_token,
    )


@pytest.mark.parametrize("qualified", [False, True])
def test_pending_intent_blocks_export_before_broker_sources_capacity_or_logs(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    qualified: bool,
) -> None:
    monkeypatch.setattr(backend_module, "runtime_release_qualified", lambda: qualified)
    stack = _stack()
    snapshot = _current_snapshot(stack.manifests.load(CASE_ID).snapshot)
    editor = RedactionToolsState.from_snapshot(snapshot, PAGE_ID).create(id_factory=lambda: MARK_ID)
    candidate = editor.to_candidate(snapshot)
    marked = ManifestSnapshot(
        snapshot.case_id,
        snapshot.schema_version,
        snapshot.revision,
        candidate,
        snapshot.sha256,
        snapshot.cas_token,
    )
    stack.manifests.result = ManifestLoadResult(ManifestLoadStatus.CURRENT, marked)
    stack.events.clear()

    with caplog.at_level(logging.DEBUG), pytest.raises(RedactionError) as failure:
        _export(stack)

    assert failure.value.code is RedactionErrorCode.PIPELINE_DISABLED
    assert stack.events == ["manifest.load"]
    assert stack.sources.calls == 0
    rendered = f"{failure.value!s}\n{failure.value!r}\n{editor!r}\n{caplog.text}"
    assert "PRIVATE_SOURCE_NAME_CANARY" not in rendered
    assert str(MARK_ID) not in rendered
    safe = sanitize_ui_failure(failure.value)
    assert safe.code == RedactionErrorCode.PIPELINE_DISABLED.value
    assert not safe.retryable
    if qualified:
        assert safe.message == (
            "Saved removal marks require Export removal derivative. "
            "Ordinary export cannot apply or verify these marks."
        )
        assert "unavailable until" not in safe.message
    else:
        assert "No information has been removed" in safe.message


def test_reflected_announcement_is_never_accepted_for_save_or_routine_status() -> None:
    stack = _stack()
    snapshot = _current_snapshot(stack.manifests.load(CASE_ID).snapshot)
    editor = RedactionToolsState.from_snapshot(snapshot, PAGE_ID)
    object.__setattr__(editor, "announcement", "PRIVATE_ANNOUNCEMENT_CANARY")
    with pytest.raises(RedactionError) as failure:
        editor.matches_opening_facts(())
    assert "PRIVATE_ANNOUNCEMENT_CANARY" not in str(failure.value)


@pytest.mark.parametrize("searchable", [False, True])
def test_direct_export_plan_cannot_bypass_saved_marks_at_original_source_boundary(
    monkeypatch: pytest.MonkeyPatch,
    searchable: bool,
) -> None:
    stack = _stack()
    snapshot = _current_snapshot(stack.manifests.load(CASE_ID).snapshot)
    plan = (
        build_searchable_pdf_export_plan(snapshot)
        if searchable
        else build_pdf_export_plan(snapshot)
    )
    # A direct caller can author plan models; source storage must inspect the saved manifest too.
    editor = RedactionToolsState.from_snapshot(snapshot, PAGE_ID).create(id_factory=lambda: MARK_ID)
    marked_document = editor.to_candidate(snapshot)
    retained = SimpleNamespace(
        revision=snapshot.revision,
        sha256=snapshot.sha256,
        cas_token=snapshot.cas_token,
        document=marked_document,
    )
    authority = SimpleNamespace()
    manager = SimpleNamespace(authenticate_case=lambda *_args, **_kwargs: nullcontext(authority))
    store = ingest_storage.WindowsSourceIngestStore(
        cast(CaseWorkspaceManager, manager),
        cast(WindowsSourcePrechecker, object()),
        maximum_page_count=4096,
    )
    monkeypatch.setattr(
        ingest_storage,
        "_read_retained_manifest_snapshot",
        lambda _authority: (retained, nullcontext()),
    )
    originals_opened = False

    def forbid_originals(*_args: object, **_kwargs: object) -> object:
        nonlocal originals_opened
        originals_opened = True
        raise AssertionError("Originals must remain unopened")

    monkeypatch.setattr(ingest_storage, "open_handle", forbid_originals)
    with pytest.raises(RedactionError) as failure:
        if searchable:
            assert type(plan) is SearchablePdfExportPlan
            store.borrow_committed_searchable_pdf_sources(
                plan, cancellation=lambda: False, deadline=lambda: False
            )
        else:
            assert type(plan) is PdfExportPlan
            store.borrow_committed_pdf_sources(
                plan, cancellation=lambda: False, deadline=lambda: False
            )
    assert failure.value.code is RedactionErrorCode.PIPELINE_DISABLED
    assert not originals_opened

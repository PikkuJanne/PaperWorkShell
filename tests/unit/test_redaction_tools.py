"""Intent editing, authenticated atomic saves, recovery, and closed export gates."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest

from paperworkshell.app.redaction_tools import (
    RedactionToolsState,
    is_redaction_announcement_safe,
    redaction_record_summary,
)
from paperworkshell.domain.manifest_repository import ManifestRepositoryError
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.pdf_export import build_pdf_export_plan, build_pdf_export_plan_for_pages
from paperworkshell.domain.redaction import (
    RedactionError,
    RedactionErrorCode,
    RedactionMark,
    RedactionMarkCollection,
)
from paperworkshell.domain.searchable_pdf_export import build_searchable_pdf_export_plan
from paperworkshell.overlays.exporting import build_flattened_pdf_export_plan
from tests.unit.test_organizer_backend import CASE_ID, PAGE_IDS, _backend, _document, _snapshot

MARK_ID = UUID("80100000-0000-4000-8000-000000000001")


def _current_document() -> dict[str, object]:
    document = _document()
    document["redaction_marks"] = []
    return document


def test_numeric_draft_history_and_summaries_keep_marks_unapplied() -> None:
    document = _current_document()
    snapshot = _snapshot(document)
    opening = RedactionToolsState.from_snapshot(snapshot, PAGE_IDS[0])
    created = opening.create(id_factory=lambda: MARK_ID)
    box = NormalizedPageBox(200_000, 300_000, 400_000, 50_000)
    edited = created.replace_selected_box(box)
    assert edited.selected_record is not None and edited.selected_record.box == box
    assert edited.changed and edited.can_undo and not edited.can_redo
    assert edited.undo().selected_record == created.selected_record
    assert edited.undo().redo().selected_record == edited.selected_record
    assert not created.undo().changed
    assert not edited.delete_selected().records
    candidate = edited.to_candidate(snapshot)
    marks = RedactionMarkCollection.from_manifest_document(candidate).marks
    assert len(marks) == 1 and marks[0].state.value == "unapplied"
    assert document == snapshot.document
    summary = redaction_record_summary(marks[0], 1, 1)
    assert "Unapplied" in summary and "Source information remains" in summary
    assert str(MARK_ID) not in summary and str(MARK_ID) not in repr(edited)
    assert is_redaction_announcement_safe(edited.announcement)
    assert not is_redaction_announcement_safe("PRIVATE_REMOVAL_CANARY")


def test_cancel_discards_draft_and_save_survives_next_page_edit() -> None:
    backend, repository, _, _, _ = _backend(_current_document())
    backend.open_case(CASE_ID)
    opening = backend.begin_redaction_tools(PAGE_IDS[0])
    backend.cancel_redaction_tools(opening.create(id_factory=lambda: MARK_ID))
    assert repository.save_calls == 0
    editor = backend.begin_redaction_tools(PAGE_IDS[0]).create(id_factory=lambda: MARK_ID)
    saved = backend.save_redaction_tools(editor)
    assert saved.revision == 1 and not saved.autosave_pending
    backend.rotate_clockwise(PAGE_IDS[0])
    backend.flush_autosave()
    assert (
        repository.current.document["redaction_marks"]
        == editor.to_candidate(_snapshot(_current_document()))["redaction_marks"]
    )
    assert repository.current.document["sources"] == _current_document()["sources"]


def test_save_failure_retains_exact_retry_and_prevents_false_cancel() -> None:
    backend, repository, _, _, _ = _backend(_current_document())
    backend.open_case(CASE_ID)
    editor = backend.begin_redaction_tools(PAGE_IDS[0]).create(id_factory=lambda: MARK_ID)
    repository.fail_saves = 1
    with pytest.raises(ManifestRepositoryError):
        backend.save_redaction_tools(editor)
    assert backend.state.autosave_pending
    with pytest.raises(RedactionError):
        backend.cancel_redaction_tools(editor)
    with pytest.raises(RedactionError):
        backend.abort_redaction_tools()
    with pytest.raises(RedactionError):
        backend.rotate_clockwise(PAGE_IDS[0])
    result = backend.save_redaction_tools(editor)
    assert result.revision == 1 and not result.autosave_pending
    assert repository.save_calls == 2


def test_rejected_old_session_and_reflected_foreign_page_do_not_save() -> None:
    backend, repository, _, _, _ = _backend(_current_document())
    backend.open_case(CASE_ID)
    prior = backend.begin_redaction_tools(PAGE_IDS[0])
    backend.cancel_redaction_tools(prior)
    current = backend.begin_redaction_tools(PAGE_IDS[0])
    with pytest.raises(RedactionError):
        backend.save_redaction_tools(prior.create(id_factory=lambda: MARK_ID))
    forged = replace(current)
    foreign_mark = RedactionMark(MARK_ID, PAGE_IDS[1], NormalizedPageBox(0, 0, 10_000, 10_000))
    object.__setattr__(forged, "steps", ((), (foreign_mark,)))
    object.__setattr__(forged, "cursor", 1)
    with pytest.raises(RedactionError):
        backend.save_redaction_tools(forged)
    assert repository.save_calls == 0
    backend.cancel_redaction_tools(current)


def test_reflected_binding_cannot_mutate_private_authority() -> None:
    backend, repository, _, _, _ = _backend(_current_document())
    backend.open_case(CASE_ID)
    state = backend.begin_redaction_tools(PAGE_IDS[0])
    object.__setattr__(state, "page_id", PAGE_IDS[1])
    with pytest.raises(RedactionError):
        backend.save_redaction_tools(state.create(id_factory=lambda: MARK_ID))
    assert repository.save_calls == 0
    backend.abort_redaction_tools()


@pytest.mark.parametrize(
    "builder",
    [
        build_pdf_export_plan,
        build_searchable_pdf_export_plan,
        lambda snapshot: build_flattened_pdf_export_plan(snapshot, {}),
    ],
)
def test_all_pdf_plan_paths_block_unapplied_active_marks(builder: object) -> None:
    document = _current_document()
    state = RedactionToolsState.from_snapshot(_snapshot(document), PAGE_IDS[0]).create(
        id_factory=lambda: MARK_ID
    )
    snapshot = _snapshot(state.to_candidate(_snapshot(document)))
    assert callable(builder)
    with pytest.raises(RedactionError) as error:
        builder(snapshot)
    assert error.value.code is RedactionErrorCode.PIPELINE_DISABLED
    with pytest.raises(RedactionError):
        build_pdf_export_plan_for_pages(snapshot, (PAGE_IDS[1],))


def test_organizer_export_blocks_before_unavailable_package_service_or_destination() -> None:
    backend, _, _, _, _ = _backend(_current_document())
    backend.open_case(CASE_ID)
    state = backend.begin_redaction_tools(PAGE_IDS[0]).create(id_factory=lambda: MARK_ID)
    backend.save_redaction_tools(state)
    with pytest.raises(RedactionError) as error:
        backend.export_package(
            Path("synthetic-output"),
            review_id=MARK_ID,
            signed_source_confirmed=True,
            human_review_confirmed=True,
        )
    assert error.value.code is RedactionErrorCode.PIPELINE_DISABLED


def test_removed_page_marks_persist_and_block_again_when_page_undo_restores_it() -> None:
    backend, repository, _, _, _ = _backend(_current_document())
    backend.open_case(CASE_ID)
    editor = backend.begin_redaction_tools(PAGE_IDS[0]).create(id_factory=lambda: MARK_ID)
    backend.save_redaction_tools(editor)
    backend.remove_page(PAGE_IDS[0])
    backend.flush_autosave()
    plan = build_pdf_export_plan(repository.current)
    assert tuple(page.page_id for page in plan.pages) == PAGE_IDS[1:]
    assert (
        len(RedactionMarkCollection.from_manifest_document(repository.current.document).marks) == 1
    )
    backend.undo()
    backend.flush_autosave()
    with pytest.raises(RedactionError) as error:
        build_pdf_export_plan(repository.current)
    assert error.value.code is RedactionErrorCode.PIPELINE_DISABLED

"""PWS-0811 direct PDF choices preserve intent and never require assignments."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import pytest

from paperworkshell.app.ordinary_pdf_export import (
    OrdinaryPdfExportError,
    OrdinaryPdfExportErrorCode,
    OrdinaryPdfPipeline,
    select_ordinary_pdf_pipeline,
)
from paperworkshell.app.redaction_tools import RedactionToolsState
from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    PdfExportSelection,
)
from paperworkshell.domain.redaction import RedactionError, RedactionErrorCode
from paperworkshell.domain.searchable_pdf_export import (
    SearchablePdfExportError,
    SearchablePdfExportErrorCode,
)
from paperworkshell.ui.backend import sanitize_ui_failure
from tests.unit.test_flattened_pdf_export import _snapshot as _marked_snapshot
from tests.unit.test_organizer_backend import IMAGE_PAGE_ID, PAGE_IDS, _document, _snapshot
from tests.unit.test_searchable_pdf_export_service import CASE_ID, DESTINATION, _service


def test_plain_mixed_pages_export_without_any_checklist_or_ocr() -> None:
    snapshot = _snapshot(_document(include_image=True))
    assert snapshot.document["assignments"] == []
    assert (
        select_ordinary_pdf_pipeline(snapshot, make_searchable=False)
        is OrdinaryPdfPipeline.STRUCTURAL
    )
    assert (
        select_ordinary_pdf_pipeline(snapshot, make_searchable=True)
        is OrdinaryPdfPipeline.SEARCHABLE
    )


def test_saved_visible_pdf_marks_select_flattened_pipeline() -> None:
    snapshot = _marked_snapshot()
    assert (
        select_ordinary_pdf_pipeline(snapshot, make_searchable=False)
        is OrdinaryPdfPipeline.FLATTENED
    )
    with pytest.raises(OrdinaryPdfExportError) as raised:
        select_ordinary_pdf_pipeline(snapshot, make_searchable=True)
    assert raised.value.code is OrdinaryPdfExportErrorCode.COMBINATION_UNSUPPORTED
    failure = sanitize_ui_failure(raised.value, operation_id=17)
    assert failure.operation_id == 17
    assert "Nothing was exported" in failure.message
    assert "Saved edits are preserved" in failure.message


def test_mixed_marks_fail_before_export_instead_of_losing_any_mark() -> None:
    document = _document(page_count=1, include_image=True)
    marked = _marked_snapshot().document
    record = dict(cast(list[dict[str, object]], marked["overlays"])[0])
    record["page_id"] = cast(list[str], document["page_sequence"])[0]
    document["overlays"] = [record]
    snapshot = _snapshot(document)
    with pytest.raises(OrdinaryPdfExportError) as raised:
        select_ordinary_pdf_pipeline(snapshot, make_searchable=False)
    assert raised.value.code is OrdinaryPdfExportErrorCode.COMBINATION_UNSUPPORTED
    marked_range = PdfExportSelection(snapshot.case_id, snapshot.revision, (PAGE_IDS[0],))
    image_range = PdfExportSelection(snapshot.case_id, snapshot.revision, (IMAGE_PAGE_ID,))
    assert (
        select_ordinary_pdf_pipeline(snapshot, make_searchable=False, selection=marked_range)
        is OrdinaryPdfPipeline.FLATTENED
    )
    assert (
        select_ordinary_pdf_pipeline(snapshot, make_searchable=False, selection=image_range)
        is OrdinaryPdfPipeline.STRUCTURAL
    )
    with pytest.raises(OrdinaryPdfExportError) as incompatible:
        select_ordinary_pdf_pipeline(snapshot, make_searchable=True, selection=marked_range)
    assert incompatible.value.code is OrdinaryPdfExportErrorCode.COMBINATION_UNSUPPORTED


def test_range_cannot_bypass_an_unapplied_mark_on_another_active_page() -> None:
    original = _snapshot(_document(page_count=2))
    marked = RedactionToolsState.from_snapshot(original, PAGE_IDS[0]).create(
        id_factory=lambda: UUID("80100000-0000-4000-8000-000000000001")
    )
    snapshot = _snapshot(marked.to_candidate(original))
    selection = PdfExportSelection(snapshot.case_id, snapshot.revision, (PAGE_IDS[1],))
    with pytest.raises(RedactionError) as raised:
        select_ordinary_pdf_pipeline(snapshot, make_searchable=False, selection=selection)
    assert raised.value.code is RedactionErrorCode.PIPELINE_DISABLED


@pytest.mark.parametrize("page_ids", ((), (PAGE_IDS[0], PAGE_IDS[0])))
def test_range_rejects_empty_or_duplicate_selection(page_ids: tuple[UUID, ...]) -> None:
    snapshot = _snapshot(_document())
    with pytest.raises(PdfExportError) as raised:
        PdfExportSelection(snapshot.case_id, snapshot.revision, page_ids)
    assert raised.value.code is PdfExportErrorCode.RANGE_INVALID


def test_explicit_searchable_export_blocks_failed_or_blocked_ocr_before_publication() -> None:
    service, sources, broker, destination, _history, retries = _service()
    with pytest.raises(SearchablePdfExportError) as raised:
        service.export_pdf(
            CASE_ID,
            DESTINATION,
            deadline=lambda: False,
            cancellation=lambda: False,
            require_ocr_success=True,
        )
    assert raised.value.code is SearchablePdfExportErrorCode.OCR_FAILED
    assert raised.value.output_page_number == 2
    assert not destination.committed
    assert retries.lease is None
    assert sources.batch.closed
    assert broker.candidate.closed
    failure = sanitize_ui_failure(raised.value)
    assert "No PDF was saved" in failure.message
    assert "Output page 2" in failure.message
    assert failure.affected_page_number == 2

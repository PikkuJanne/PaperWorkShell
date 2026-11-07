"""PWS-0811 direct PDF choices preserve intent and never require assignments."""

from __future__ import annotations

from typing import cast

import pytest

from paperworkshell.app.ordinary_pdf_export import (
    OrdinaryPdfExportError,
    OrdinaryPdfExportErrorCode,
    OrdinaryPdfPipeline,
    select_ordinary_pdf_pipeline,
)
from paperworkshell.domain.searchable_pdf_export import (
    SearchablePdfExportError,
    SearchablePdfExportErrorCode,
)
from paperworkshell.ui.backend import sanitize_ui_failure
from tests.unit.test_flattened_pdf_export import _snapshot as _marked_snapshot
from tests.unit.test_organizer_backend import _document, _snapshot
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
    document = _document(include_image=True)
    marked = _marked_snapshot().document
    record = dict(cast(list[dict[str, object]], marked["overlays"])[0])
    record["page_id"] = cast(list[str], document["page_sequence"])[0]
    document["overlays"] = [record]
    with pytest.raises(OrdinaryPdfExportError) as raised:
        select_ordinary_pdf_pipeline(_snapshot(document), make_searchable=False)
    assert raised.value.code is OrdinaryPdfExportErrorCode.COMBINATION_UNSUPPORTED


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

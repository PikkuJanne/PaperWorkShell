"""Host registration tests for the admitted searchable exporter."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from paperworkshell.worker import host
from paperworkshell.worker.protocol.models import SEARCHABLE_PDF_EXPORT_CAPABILITY


@dataclass(frozen=True, slots=True)
class _AdmittedSearchableHandler:
    descriptor = SEARCHABLE_PDF_EXPORT_CAPABILITY


def test_host_registers_an_admitted_searchable_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "paperworkshell.worker.searchable_pdf_export_operation."
        "build_searchable_pdf_export_operation_handler",
        lambda: _AdmittedSearchableHandler(),
    )

    assert SEARCHABLE_PDF_EXPORT_CAPABILITY in host._build_operation_registry().capabilities


def test_host_omits_searchable_handler_when_admission_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "paperworkshell.worker.searchable_pdf_export_operation."
        "build_searchable_pdf_export_operation_handler",
        lambda: None,
    )

    assert SEARCHABLE_PDF_EXPORT_CAPABILITY not in host._build_operation_registry().capabilities


def test_host_omits_searchable_handler_when_admission_crashes_without_disclosure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def crash() -> None:
        raise RuntimeError("private configured executable path")

    monkeypatch.setattr(
        "paperworkshell.worker.searchable_pdf_export_operation."
        "build_searchable_pdf_export_operation_handler",
        crash,
    )

    assert SEARCHABLE_PDF_EXPORT_CAPABILITY not in host._build_operation_registry().capabilities

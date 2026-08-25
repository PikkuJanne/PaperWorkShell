"""Host registration tests for the admitted flattened-PDF exporter."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from paperworkshell.worker import host
from paperworkshell.worker.protocol.models import FLATTENED_PDF_EXPORT_CAPABILITY


@dataclass(frozen=True, slots=True)
class _AdmittedFlattenedHandler:
    descriptor = FLATTENED_PDF_EXPORT_CAPABILITY


def test_host_registers_admitted_flattened_exporter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "paperworkshell.worker.flattened_pdf_export_operation."
        "build_flattened_pdf_export_operation_handler",
        lambda: _AdmittedFlattenedHandler(),
    )

    assert FLATTENED_PDF_EXPORT_CAPABILITY in host._build_operation_registry().capabilities


def test_host_omits_unavailable_flattened_exporter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "paperworkshell.worker.flattened_pdf_export_operation."
        "build_flattened_pdf_export_operation_handler",
        lambda: None,
    )

    assert FLATTENED_PDF_EXPORT_CAPABILITY not in host._build_operation_registry().capabilities


def test_host_omits_crashing_flattened_exporter_without_disclosure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def crash() -> None:
        raise RuntimeError("private configured executable path")

    monkeypatch.setattr(
        "paperworkshell.worker.flattened_pdf_export_operation."
        "build_flattened_pdf_export_operation_handler",
        crash,
    )

    assert FLATTENED_PDF_EXPORT_CAPABILITY not in host._build_operation_registry().capabilities

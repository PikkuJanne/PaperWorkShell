"""The legacy PDF-only capability must not require the optional OCR closure."""

from __future__ import annotations

import builtins
from types import ModuleType
from typing import Any

import pytest

from paperworkshell.worker.redaction_export_operation import (
    RedactionExportOperationHandler,
    build_redaction_export_operation_handler,
)


def test_pdf_only_handler_does_not_import_unavailable_ocr_packages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PWS_TESSERACT_EXECUTABLE", raising=False)
    monkeypatch.delenv("PWS_TESSDATA_ROOT", raising=False)
    original = builtins.__import__

    def guarded(name: str, *args: Any, **kwargs: Any) -> ModuleType:
        if name in {
            "paperworkshell.ocr.ocrmypdf_adapter",
            "paperworkshell.worker.ocr_operation",
        }:
            raise ModuleNotFoundError("synthetic_optional_ocr_unavailable")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    handler = build_redaction_export_operation_handler()
    assert type(handler) is RedactionExportOperationHandler

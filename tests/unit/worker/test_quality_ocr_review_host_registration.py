from __future__ import annotations

import builtins
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pytest

from paperworkshell.worker import host
from paperworkshell.worker.process_creation_policy import WorkerProcessCreationDenied
from paperworkshell.worker.protocol.models import (
    NOOP_CAPABILITY,
    QUALITY_OCR_REVIEW_CAPABILITY,
)
from paperworkshell.worker.protocol.operation_scope import (
    PWS_WORKER_OPERATION_SCOPE_ENVIRONMENT,
    WorkerOperationScope,
)


@dataclass(frozen=True, slots=True)
class _AdmittedHandler:
    descriptor = QUALITY_OCR_REVIEW_CAPABILITY


def test_host_registers_admitted_quality_ocr_review_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "paperworkshell.worker.quality_ocr_review_operation."
        "build_quality_ocr_review_operation_handler",
        lambda: _AdmittedHandler(),
    )

    assert QUALITY_OCR_REVIEW_CAPABILITY in host._build_operation_registry().capabilities


def test_host_omits_quality_ocr_review_when_runtime_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "paperworkshell.worker.quality_ocr_review_operation."
        "build_quality_ocr_review_operation_handler",
        lambda: None,
    )

    assert QUALITY_OCR_REVIEW_CAPABILITY not in host._build_operation_registry().capabilities


def test_host_omits_crashed_admission_without_disclosing_runtime_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def crash() -> None:
        raise RuntimeError("C:/private/tesseract.exe")

    monkeypatch.setattr(
        "paperworkshell.worker.quality_ocr_review_operation."
        "build_quality_ocr_review_operation_handler",
        crash,
    )

    assert QUALITY_OCR_REVIEW_CAPABILITY not in host._build_operation_registry().capabilities


def test_quality_scope_imports_and_builds_only_noop_and_quality_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "paperworkshell.worker.quality_ocr_review_operation."
        "build_quality_ocr_review_operation_handler",
        lambda: _AdmittedHandler(),
    )
    real_import = builtins.__import__
    forbidden_imports: list[str] = []

    def guarded_import(
        name: str,
        globals: Mapping[str, object] | None = None,
        locals: Mapping[str, object] | None = None,
        fromlist: Sequence[str] = (),
        level: int = 0,
    ) -> Any:
        if name in {
            "paperworkshell.worker.ocr_operation",
            "paperworkshell.worker.searchable_pdf_export_operation",
        }:
            forbidden_imports.append(name)
            raise AssertionError("quality scope must not import full OCR handlers")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    registry = host._build_operation_registry(WorkerOperationScope.QUALITY_OCR_REVIEW)

    assert registry.capabilities == (NOOP_CAPABILITY, QUALITY_OCR_REVIEW_CAPABILITY)
    assert forbidden_imports == []


def test_host_accepts_only_the_closed_environment_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        PWS_WORKER_OPERATION_SCOPE_ENVIRONMENT,
        WorkerOperationScope.QUALITY_OCR_REVIEW.value,
    )
    assert host._read_operation_scope() is WorkerOperationScope.QUALITY_OCR_REVIEW

    monkeypatch.setenv(PWS_WORKER_OPERATION_SCOPE_ENVIRONMENT, "future-scope-v9")
    with pytest.raises(WorkerProcessCreationDenied):
        host._read_operation_scope()

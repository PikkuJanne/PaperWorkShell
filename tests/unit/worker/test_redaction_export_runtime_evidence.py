"""Runtime OCR authority is bound to the admitted composition and exact profile."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from paperworkshell.domain.pdf_export import PdfExportError
from paperworkshell.domain.redaction_export_candidate import RedactionExportWorkerReport
from paperworkshell.domain.redaction_verification import (
    RUNTIME_VERIFICATION_PROFILE,
    VERIFICATION_PROFILE,
    VerificationCode,
    VerificationMethod,
    VerificationMethodResult,
    VerificationStatus,
    run_verification_methods,
)
from paperworkshell.ocr.ocrmypdf_adapter import OcrMyPdfAdapter
from paperworkshell.worker import ocr_operation
from paperworkshell.worker.host import _build_operation_registry
from paperworkshell.worker.operations import OcrProcessOperationHandler
from paperworkshell.worker.protocol.models import NOOP_CAPABILITY, REDACTION_EXPORT_CAPABILITY
from paperworkshell.worker.protocol.operation_scope import WorkerOperationScope
from paperworkshell.worker.redaction_export_broker import _require_supported_evidence
from paperworkshell.worker.redaction_export_operation import (
    build_redaction_export_operation_handler,
)
from tests.unit.test_redaction_reconstruction_models import request
from tests.unit.worker.test_redaction_export_worker import report


def _report(profile: str, *, viewer_pass: bool = False) -> RedactionExportWorkerReport:
    result = report(request())
    intent = replace(result.verification_request, profile_id=profile)

    def clear(method: VerificationMethod) -> VerificationMethodResult:
        return VerificationMethodResult(method, VerificationStatus.PASS, VerificationCode.CLEAR)

    methods = {
        method: lambda selected=method: clear(selected)
        for method in VerificationMethod
        if method is not VerificationMethod.VIEWERS or viewer_pass
    }
    return replace(
        result,
        verification_request=intent,
        verification=run_verification_methods(intent, methods),
    )


@pytest.mark.parametrize(
    ("profile", "runtime", "viewer_pass", "allowed"),
    [
        (RUNTIME_VERIFICATION_PROFILE, True, False, True),
        (RUNTIME_VERIFICATION_PROFILE, False, False, False),
        (VERIFICATION_PROFILE, False, False, False),
        (VERIFICATION_PROFILE, True, False, False),
        (RUNTIME_VERIFICATION_PROFILE, True, True, False),
    ],
)
def test_broker_rejects_profile_substitution_and_invented_viewer_testimony(
    profile: str, runtime: bool, viewer_pass: bool, allowed: bool
) -> None:
    evidence = _report(profile, viewer_pass=viewer_pass)
    if allowed:
        _require_supported_evidence(evidence, runtime_ocr=runtime)
        assert evidence.verification.runtime_ready
        assert not evidence.verification.release_ready
    else:
        with pytest.raises(PdfExportError):
            _require_supported_evidence(evidence, runtime_ocr=runtime)


def test_engine_configuration_cannot_advertise_runtime_ocr_without_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PWS_TESSERACT_EXECUTABLE", "unavailable")
    monkeypatch.setenv("PWS_TESSDATA_ROOT", "unavailable")
    monkeypatch.setattr(ocr_operation, "build_ocr_process_operation_handler", lambda: None)
    assert build_redaction_export_operation_handler() is None
    assert _build_operation_registry(WorkerOperationScope.REDACTION_EXPORT).capabilities == (
        NOOP_CAPABILITY,
    )


def test_exact_admitted_ocr_processor_is_the_only_runtime_injection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    processor = OcrMyPdfAdapter(tmp_path / "tesseract.exe", tmp_path)
    admitted = OcrProcessOperationHandler(
        ocr_operation.WindowsOcrProcessAdapter(candidate_processor=processor)
    )
    monkeypatch.setenv("PWS_TESSERACT_EXECUTABLE", str(processor.tesseract_executable))
    monkeypatch.setenv("PWS_TESSDATA_ROOT", str(processor.tessdata_directory))
    monkeypatch.setattr(ocr_operation, "build_ocr_process_operation_handler", lambda: admitted)
    handler = build_redaction_export_operation_handler()
    assert handler is not None and handler.descriptor == REDACTION_EXPORT_CAPABILITY
    assert handler.adapter.exporter.ocr is processor  # type: ignore[attr-defined]

"""The opt-in stage composition retains the admitted export runtime boundary."""

from __future__ import annotations

import builtins
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest

from paperworkshell.worker import pdf_image_stage_composition as composition
from paperworkshell.worker.client.errors import WorkerClientError
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.host import _build_operation_registry
from paperworkshell.worker.protocol.models import PDF_IMAGE_STAGE_CAPABILITY
from paperworkshell.worker.protocol.operation_scope import WorkerOperationScope
from tests.unit.worker.test_pdf_export_composition import _config, _FakeWorkerClient


def _is_closed(runtime: composition.PdfImageStageWorkerComposition) -> bool:
    return runtime.closed


def test_composition_requests_only_stage_capability_and_revokes_on_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(composition, "WorkerClient", _FakeWorkerClient)
    heavy = HeavyOperationCoordinator(1)
    with composition.create_pdf_image_stage_worker_composition(
        config, heavy_operations=heavy
    ) as runtime:
        worker = cast(_FakeWorkerClient, runtime._worker)
        assert worker.required_capabilities == (PDF_IMAGE_STAGE_CAPABILITY,)
        assert runtime.image_stage_processor.heavy_operations is heavy
        assert runtime.resource_snapshot() == worker.snapshot
        assert runtime.image_stage_processor._policy.limits == config.export_policy.limits
        assert not _is_closed(runtime)
        port = runtime.image_stage_processor
    assert port.revoked and _is_closed(runtime)
    runtime.close()
    assert worker.terminate_calls == 1
    with pytest.raises(WorkerClientError):
        runtime.__enter__()


def test_stage_registration_is_separate_and_not_available_in_ocr_review_scope() -> None:
    assert PDF_IMAGE_STAGE_CAPABILITY in _build_operation_registry().capabilities
    assert (
        PDF_IMAGE_STAGE_CAPABILITY
        not in _build_operation_registry(WorkerOperationScope.QUALITY_OCR_REVIEW).capabilities
    )


def test_missing_pillow_omits_stage_capability_even_with_default_export_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = builtins.__import__
    attempted = False

    def without_pillow(
        name: str,
        globals: Mapping[str, object] | None = None,
        locals: Mapping[str, object] | None = None,
        fromlist: Sequence[str] = (),
        level: int = 0,
    ) -> ModuleType:
        nonlocal attempted
        if name == "PIL" or name.startswith("PIL."):
            attempted = True
            raise ModuleNotFoundError("PRIVATE-MISSING-PILLOW-PATH")
        return real_import(name, globals, locals, fromlist, level)

    for module in (
        "paperworkshell.worker.pdf_image_stage_operation",
        "paperworkshell.pdf.image_stage_adapter",
        "paperworkshell.pdf.image_stage_encoder",
        "paperworkshell.imaging.pdf_image_encoder",
    ):
        monkeypatch.delitem(sys.modules, module, raising=False)
    monkeypatch.setattr(builtins, "__import__", without_pillow)
    assert PDF_IMAGE_STAGE_CAPABILITY not in _build_operation_registry().capabilities
    assert attempted

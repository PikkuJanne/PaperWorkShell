"""Closed desktop capability admission without importing unrelated export/OCR engines."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from paperworkshell.worker import host
from paperworkshell.worker.process_creation_policy import WorkerProcessCreationDenied
from paperworkshell.worker.protocol.models import (
    IMAGE_NORMALIZE_CAPABILITY,
    IMAGE_TRANSFORM_CAPABILITY,
    NOOP_CAPABILITY,
    PDF_EXPORT_CAPABILITY,
    PDF_INSPECT_CAPABILITY,
    PDF_THUMBNAIL_CAPABILITY,
    SIGNATURE_IMAGE_RASTER_CAPABILITY,
    CapabilityDescriptor,
)
from paperworkshell.worker.protocol.operation_scope import WorkerOperationScope
from paperworkshell.worker.protocol.text_companion import TEXT_COMPANION_CAPABILITY

DESKTOP = (
    NOOP_CAPABILITY,
    TEXT_COMPANION_CAPABILITY,
    IMAGE_NORMALIZE_CAPABILITY,
    SIGNATURE_IMAGE_RASTER_CAPABILITY,
    IMAGE_TRANSFORM_CAPABILITY,
    PDF_INSPECT_CAPABILITY,
    PDF_THUMBNAIL_CAPABILITY,
)
IMAGES = (NOOP_CAPABILITY, IMAGE_NORMALIZE_CAPABILITY, IMAGE_TRANSFORM_CAPABILITY)
FACTORIES = (
    (
        "text_companion_operation",
        "build_text_companion_operation_handler",
        TEXT_COMPANION_CAPABILITY,
    ),
    (
        "image_normalize_operation",
        "build_image_normalize_operation_handler",
        IMAGE_NORMALIZE_CAPABILITY,
    ),
    (
        "signature_image_raster_operation",
        "build_signature_image_raster_operation_handler",
        SIGNATURE_IMAGE_RASTER_CAPABILITY,
    ),
    (
        "image_transform_operation",
        "build_image_transform_operation_handler",
        IMAGE_TRANSFORM_CAPABILITY,
    ),
    ("pdf_operation", "build_pdf_inspection_operation_handler", PDF_INSPECT_CAPABILITY),
    ("pdf_thumbnail_operation", "build_pdf_thumbnail_operation_handler", PDF_THUMBNAIL_CAPABILITY),
)


def test_desktop_admits_only_its_real_handlers_and_rejects_export_lookup() -> None:
    registry = host._build_operation_registry(WorkerOperationScope.DESKTOP)

    assert registry.capabilities == DESKTOP
    with pytest.raises(LookupError, match="not registered"):
        registry.handler_for(PDF_EXPORT_CAPABILITY)


@pytest.mark.parametrize("module_name,factory_name,descriptor", (FACTORIES[1], FACTORIES[3]))
def test_image_scope_rejects_unavailable_adapter_and_excludes_pdf(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    factory_name: str,
    descriptor: object,
) -> None:
    module = importlib.import_module("paperworkshell.worker." + module_name)

    def unavailable() -> object:
        raise RuntimeError("PRIVATE_ADAPTER_PATH_CANARY")

    monkeypatch.setattr(module, factory_name, unavailable)
    registry = host._build_operation_registry(WorkerOperationScope.IMAGE_PAGES)

    assert registry.capabilities == tuple(value for value in IMAGES if value != descriptor)
    with pytest.raises(LookupError, match="not registered"):
        registry.handler_for(PDF_INSPECT_CAPABILITY)


@pytest.mark.parametrize("module_name,factory_name,descriptor", FACTORIES)
def test_unavailable_desktop_adapter_is_not_advertised(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    factory_name: str,
    descriptor: object,
) -> None:
    module = importlib.import_module("paperworkshell.worker." + module_name)

    def unavailable() -> object:
        raise RuntimeError("PRIVATE_ADAPTER_PATH_CANARY")

    monkeypatch.setattr(module, factory_name, unavailable)
    registry = host._build_operation_registry(WorkerOperationScope.DESKTOP)

    assert registry.capabilities == tuple(value for value in DESKTOP if value != descriptor)


def test_desktop_scope_environment_is_closed_and_unknown_values_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PWS_WORKER_OPERATION_SCOPE", "desktop-v1")
    assert host._read_operation_scope() is WorkerOperationScope.DESKTOP
    monkeypatch.setenv("PWS_WORKER_OPERATION_SCOPE", "image-pages-v1")
    assert host._read_operation_scope() is WorkerOperationScope.IMAGE_PAGES
    monkeypatch.setenv("PWS_WORKER_OPERATION_SCOPE", "desktop-v2")
    with pytest.raises(WorkerProcessCreationDenied):
        host._read_operation_scope()
    monkeypatch.delenv("PWS_WORKER_OPERATION_SCOPE")
    assert host._read_operation_scope() is WorkerOperationScope.ALL


@pytest.mark.parametrize(
    ("scope", "descriptors"),
    ((WorkerOperationScope.DESKTOP, DESKTOP), (WorkerOperationScope.IMAGE_PAGES, IMAGES)),
)
def test_scoped_registry_imports_no_unneeded_native_engine_in_a_fresh_interpreter(
    scope: WorkerOperationScope, descriptors: tuple[CapabilityDescriptor, ...]
) -> None:
    source_root = Path(__file__).resolve().parents[3] / "src"
    probe = """
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from paperworkshell.worker import host
from paperworkshell.worker.protocol.operation_scope import WorkerOperationScope
host._cache_windows_platform_family()
scope=WorkerOperationScope(sys.argv[2])
registry=host._build_operation_registry(scope)
assert Path(host.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())
blocked=('ocrmypdf','pydantic','fpdf','fontTools','uharfbuzz',
         'paperworkshell.worker.pdf_export_operation',
         'paperworkshell.worker.pdf_image_stage_operation',
         'paperworkshell.worker.flattened_pdf_export_operation',
         'paperworkshell.worker.ocr_operation',
         'paperworkshell.worker.quality_ocr_review_operation',
         'paperworkshell.worker.searchable_pdf_export_operation')
if scope is WorkerOperationScope.IMAGE_PAGES:
    blocked+=('pikepdf','pypdfium2','paperworkshell.worker.pdf_operation',
              'paperworkshell.worker.pdf_thumbnail_operation',
              'paperworkshell.worker.signature_image_raster_operation',
              'paperworkshell.worker.text_companion_operation')
unexpected=sorted(name for name in sys.modules if any(name==p or name.startswith(p+'.') for p in blocked))
assert not unexpected,unexpected
print(json.dumps([[value.capability_id,value.capability_version] for value in registry.capabilities]))
"""
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith(("PWS_", "PYTHON"))
    }
    completed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", probe, str(source_root), scope.value],
        cwd=source_root.parent,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        shell=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == [
        [value.capability_id, value.capability_version] for value in descriptors
    ]

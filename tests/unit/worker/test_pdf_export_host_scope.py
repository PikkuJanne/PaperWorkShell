"""The structural export worker loads only its existing versioned handlers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from paperworkshell.worker import host
from paperworkshell.worker.process_creation_policy import WorkerProcessCreationDenied
from paperworkshell.worker.protocol.models import (
    IMAGE_PDF_EXPORT_CAPABILITY,
    NOOP_CAPABILITY,
    PDF_EXPORT_CAPABILITY,
    PORTAL_PDF_EXPORT_CAPABILITY,
)
from paperworkshell.worker.protocol.operation_scope import (
    PWS_WORKER_OPERATION_SCOPE_ENVIRONMENT,
    WorkerOperationScope,
)

_EXPECTED = (
    NOOP_CAPABILITY,
    PDF_EXPORT_CAPABILITY,
    IMAGE_PDF_EXPORT_CAPABILITY,
    PORTAL_PDF_EXPORT_CAPABILITY,
)


def test_structural_export_scope_never_imports_unrelated_handlers() -> None:
    source = Path(__file__).resolve().parents[3] / "src"
    probe = """
import builtins
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from paperworkshell.worker import host
from paperworkshell.worker.protocol.operation_scope import WorkerOperationScope

assert Path(host.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())
host._cache_windows_platform_family()
blocked = (
    'ocrmypdf', 'pydantic', 'fpdf', 'fontTools', 'uharfbuzz',
    'paperworkshell.worker.text_companion_operation',
    'paperworkshell.worker.image_normalize_operation',
    'paperworkshell.worker.signature_image_raster_operation',
    'paperworkshell.worker.image_transform_operation',
    'paperworkshell.worker.pdf_operation',
    'paperworkshell.worker.pdf_thumbnail_operation',
    'paperworkshell.worker.pdf_image_stage_operation',
    'paperworkshell.worker.flattened_pdf_export_operation',
    'paperworkshell.worker.ocr_operation',
    'paperworkshell.worker.quality_ocr_review_operation',
    'paperworkshell.worker.searchable_pdf_export_operation',
)

def is_blocked(name):
    return any(name == prefix or name.startswith(prefix + '.') for prefix in blocked)

real_import = builtins.__import__
attempted = []

def guarded_import(name, *args, **kwargs):
    if is_blocked(name):
        attempted.append(name)
        raise AssertionError('unexpected import in structural export scope')
    return real_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
registry = host._build_operation_registry(WorkerOperationScope.PDF_EXPORT)
assert not attempted, attempted
assert not any(is_blocked(name) for name in sys.modules)
print(json.dumps([[value.capability_id, value.capability_version] for value in registry.capabilities]))
"""
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith(("PWS_", "PYTHON"))
    }
    completed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", probe, str(source)],
        cwd=source.parent,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        shell=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == [
        [value.capability_id, value.capability_version] for value in _EXPECTED
    ]


def test_structural_export_scope_omits_unavailable_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(*, operation_version: int = 1) -> None:
        raise RuntimeError("synthetic unavailable PDF export adapter")

    monkeypatch.setattr(
        "paperworkshell.worker.pdf_export_operation.build_pdf_export_operation_handler",
        unavailable,
    )

    registry = host._build_operation_registry(WorkerOperationScope.PDF_EXPORT)

    assert registry.capabilities == (NOOP_CAPABILITY,)


def test_structural_export_scope_requires_the_exact_closed_environment_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(PWS_WORKER_OPERATION_SCOPE_ENVIRONMENT, "pdf-export-v1")
    assert host._read_operation_scope() is WorkerOperationScope.PDF_EXPORT
    monkeypatch.setenv(PWS_WORKER_OPERATION_SCOPE_ENVIRONMENT, "pdf-export-v2")
    with pytest.raises(WorkerProcessCreationDenied):
        host._read_operation_scope()

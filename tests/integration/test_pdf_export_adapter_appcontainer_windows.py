"""Live restricted-process evidence for the real PWS-0112 native adapter closure."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from secrets import token_hex
from typing import Any
from uuid import uuid4

import pikepdf
import pypdfium2  # type: ignore[import-untyped]
import pypdfium2_raw  # type: ignore[import-untyped]
import pytest

from paperworkshell.pdf.qpdf_checker import DEFAULT_QPDF_EXECUTABLE
from paperworkshell.worker.client._windows import WindowsLaunchConfig, WindowsProcessLauncher
from paperworkshell.worker.client.process import (
    TransportReceiveKind,
    WorkerLaunchRequest,
    WorkerResourcePolicy,
)
from paperworkshell.worker.protocol.models import SessionCapability
from tests.worker_resources import WorkerTestResources

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="PWS-0112 export-adapter containment evidence targets supported Windows",
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE = (
    _PROJECT_ROOT / "tests" / "fixtures" / "synthetic" / "v1" / "pdf" / "render-calibration.pdf"
)
_PROBE_SOURCE_NAME = "pdf-export-adapter-probe.pdf"
_PACKAGE_DIRECTORIES = (
    "pikepdf",
    "pikepdf.libs",
    "lxml",
    "PIL",
    "packaging",
    "pypdfium2",
    "pypdfium2_cfg",
    "pypdfium2_raw",
)


def _stage_pdf_engines(resources: WorkerTestResources) -> None:
    roots = {
        Path(pikepdf.__file__).resolve().parent.parent,
        Path(pypdfium2.__file__).resolve().parent.parent,
        Path(pypdfium2_raw.__file__).resolve().parent.parent,
    }
    assert len(roots) == 1
    source_root = roots.pop()
    destination_root = resources.runtime_root / "Lib" / "site-packages"
    destination_root.mkdir(exist_ok=True)
    for name in _PACKAGE_DIRECTORIES:
        source = source_root / name
        destination = destination_root / name
        assert source.is_dir()
        if not destination.is_dir():
            shutil.copytree(
                source,
                destination,
                copy_function=shutil.copyfile,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
            )
    shutil.copyfile(_FIXTURE, resources.helper_root / _PROBE_SOURCE_NAME)


def _qpdf_executable() -> Path:
    configured = os.environ.get("PWS_QPDF_EXECUTABLE")
    selected = Path(configured) if configured else DEFAULT_QPDF_EXECUTABLE
    assert selected.is_absolute() and selected.is_file(), "required qpdf 12.3.2 is unavailable"
    return selected


def test_real_export_adapter_succeeds_inside_appcontainer(
    worker_test_resources: WorkerTestResources,
) -> None:
    _stage_pdf_engines(worker_test_resources)
    qpdf = _qpdf_executable()
    launcher = WindowsProcessLauncher(
        WindowsLaunchConfig(
            python_executable=worker_test_resources.python_executable,
            bootstrap_script=(
                worker_test_resources.helper_root / "pdf_export_adapter_appcontainer_probe.py"
            ),
            runtime_roots=(worker_test_resources.runtime_root,),
            code_roots=(
                worker_test_resources.source_root,
                worker_test_resources.helper_root,
            ),
            working_directory=worker_test_resources.helper_root,
            qpdf_executable=qpdf,
        )
    )
    process = launcher.launch(
        WorkerLaunchRequest(
            case_id=uuid4(),
            session_id=uuid4(),
            worker_instance_id=uuid4(),
            session_capability=SessionCapability(token_hex(32)),
            resource_policy=WorkerResourcePolicy(
                process_memory_bytes=512 * 1024 * 1024,
                job_memory_bytes=768 * 1024 * 1024,
                cpu_time_ms=60_000,
                maximum_processes=2,
            ),
        )
    )
    try:
        received = process.transport.receive_payload(40.0)
        assert received.kind is TransportReceiveKind.PAYLOAD
        assert received.payload is not None
        result: dict[str, Any] = json.loads(received.payload)
        assert process.wait(20.0) == 0
    finally:
        process.close()

    assert result.get("ok") is True, json.dumps(result, sort_keys=True)
    assert result == {
        "app_container": True,
        "check_count": 6,
        "in_job": True,
        "minimal_environment": True,
        "ok": True,
        "operation_output_byte_size": result["operation_output_byte_size"],
        "output_byte_size": result["output_byte_size"],
        "page_count": 2,
    }
    assert type(result["operation_output_byte_size"]) is int
    assert result["operation_output_byte_size"] == result["output_byte_size"]
    assert type(result["output_byte_size"]) is int
    assert result["output_byte_size"] > 0

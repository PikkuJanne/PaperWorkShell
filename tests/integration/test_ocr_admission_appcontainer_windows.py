"""Live, content-free admission evidence for the PWS-0204 AppContainer."""

from __future__ import annotations

import json
import os
import shutil
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from secrets import token_hex
from typing import Any
from uuid import uuid4

import pytest
from packaging.requirements import Requirement

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
    reason="PWS-0204 OCR admission targets the supported Windows AppContainer",
)


def _stage_ocr_python_closure(resources: WorkerTestResources) -> None:
    destination_root = resources.runtime_root / "Lib" / "site-packages"
    destination_root.mkdir(exist_ok=True)
    pending = ["ocrmypdf"]
    completed: set[str] = set()
    while pending:
        selected = pending.pop()
        normalized = selected.casefold().replace("_", "-")
        if normalized in completed:
            continue
        try:
            package = distribution(selected)
        except PackageNotFoundError:
            pytest.fail("the locked OCRmyPDF test dependency closure is unavailable")
        completed.add(normalized)
        for raw in package.requires or ():
            requirement = Requirement(raw)
            if requirement.marker is None or requirement.marker.evaluate():
                pending.append(requirement.name)
        package_root = Path(str(package.locate_file(""))).resolve()
        for entry in package.files or ():
            relative = Path(entry)
            source = Path(str(package.locate_file(entry))).resolve()
            try:
                contained = source.relative_to(package_root)
            except ValueError:
                continue
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or not source.is_file()
                or source.suffix.casefold() in {".pyc", ".pyo"}
                or "__pycache__" in contained.parts
            ):
                continue
            destination = destination_root / contained
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                assert destination.is_file()
                assert source.read_bytes() == destination.read_bytes()
            else:
                shutil.copyfile(source, destination)


def _runtime_from_environment() -> tuple[Path, Path]:
    executable = os.environ.get("PWS_TESSERACT_EXECUTABLE")
    tessdata = os.environ.get("PWS_TESSDATA_ROOT")
    if type(executable) is not str or not executable or type(tessdata) is not str or not tessdata:
        pytest.skip("the admitted PWS-0204 Tesseract runtime is not staged")
    return Path(executable), Path(tessdata)


def test_exact_ocr_admission_succeeds_inside_appcontainer(
    worker_test_resources: WorkerTestResources,
) -> None:
    _stage_ocr_python_closure(worker_test_resources)
    tesseract, tessdata = _runtime_from_environment()
    launcher = WindowsProcessLauncher(
        WindowsLaunchConfig(
            python_executable=worker_test_resources.python_executable,
            bootstrap_script=(
                worker_test_resources.helper_root / "ocr_admission_appcontainer_probe.py"
            ),
            runtime_roots=(worker_test_resources.runtime_root,),
            code_roots=(
                worker_test_resources.source_root,
                worker_test_resources.helper_root,
            ),
            working_directory=worker_test_resources.helper_root,
            tesseract_executable=tesseract,
            tessdata_root=tessdata,
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
                cpu_time_ms=30_000,
                maximum_processes=2,
            ),
        )
    )
    try:
        received = process.transport.receive_payload(30.0)
        assert received.kind is TransportReceiveKind.PAYLOAD
        assert received.payload is not None
        result: dict[str, Any] = json.loads(received.payload)
        assert process.wait(15.0) == 0
    finally:
        process.close()

    assert result.get("ok") is True, json.dumps(result, sort_keys=True)
    assert result == {
        "app_container": True,
        "audit_event_allowed": True,
        "audit_event_seen": True,
        "english_available": True,
        "environment_complete": True,
        "error_code": "",
        "executable_absolute": True,
        "executable_file": True,
        "handler_available": True,
        "in_job": True,
        "metadata_exact": True,
        "minimal_environment": True,
        "module_exact": True,
        "ok": True,
        "resources_available": True,
        "stage": "complete",
        "temporary_absolute": True,
        "temporary_directory": True,
        "tessdata_absolute": True,
        "tessdata_directory": True,
        "version_exact": True,
    }

"""Live AppContainer evidence for the real PWS-0201 image adapter closure."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from secrets import token_hex
from typing import Any
from uuid import uuid4

import pytest
from PIL import Image

from paperworkshell.domain.image_pages import IMAGE_PAGE_ARTIFACT_HEADER_BYTES
from paperworkshell.worker.client._windows import WindowsLaunchConfig, WindowsProcessLauncher
from paperworkshell.worker.client.process import (
    TransportReceiveKind,
    WorkerLaunchRequest,
    WorkerResourcePolicy,
)
from paperworkshell.worker.protocol.models import SessionCapability
from tests.worker_resources import (
    WorkerTestResources,
)

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="PWS-0201 image-adapter containment evidence targets supported Windows",
)

_PROBE_SOURCE_NAME = "image-normalize-adapter-probe.png"


def _stage_synthetic_png(resources: WorkerTestResources, tmp_path: Path) -> bytes:
    generated = tmp_path / "synthetic-image-normalize-probe.png"
    with Image.new("RGBA", (4, 2), (200, 20, 10, 96)) as image:
        image.save(generated, format="PNG")
    destination = resources.helper_root / _PROBE_SOURCE_NAME
    assert not os.path.lexists(destination)
    shutil.copyfile(generated, destination)
    payload = destination.read_bytes()
    assert payload.startswith(b"\x89PNG\r\n\x1a\n")
    return payload


def test_real_image_normalize_adapter_succeeds_inside_appcontainer(
    tmp_path: Path,
    worker_test_resources: WorkerTestResources,
) -> None:
    original = _stage_synthetic_png(worker_test_resources, tmp_path)
    staged_source = worker_test_resources.helper_root / _PROBE_SOURCE_NAME
    launcher = WindowsProcessLauncher(
        WindowsLaunchConfig(
            python_executable=worker_test_resources.python_executable,
            bootstrap_script=(
                worker_test_resources.helper_root / "image_normalize_adapter_appcontainer_probe.py"
            ),
            runtime_roots=(worker_test_resources.runtime_root,),
            code_roots=(
                worker_test_resources.source_root,
                worker_test_resources.helper_root,
            ),
            working_directory=worker_test_resources.helper_root,
        )
    )
    process = launcher.launch(
        WorkerLaunchRequest(
            case_id=uuid4(),
            session_id=uuid4(),
            worker_instance_id=uuid4(),
            session_capability=SessionCapability(token_hex(32)),
            resource_policy=WorkerResourcePolicy(
                process_memory_bytes=256 * 1024 * 1024,
                job_memory_bytes=384 * 1024 * 1024,
                cpu_time_ms=30_000,
                maximum_processes=1,
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

    assert staged_source.read_bytes() == original
    assert result.get("ok") is True, json.dumps(result, sort_keys=True)
    assert result == {
        "app_container": True,
        "error_code": "",
        "error_context": "",
        "height_pixels": 1,
        "in_job": True,
        "minimal_environment": True,
        "ok": True,
        "opaque_output": True,
        "output_byte_size": IMAGE_PAGE_ARTIFACT_HEADER_BYTES + 4,
        "source_color_model": "rgba",
        "source_has_alpha": True,
        "source_immutable": True,
        "stage": "complete",
        "width_pixels": 1,
    }

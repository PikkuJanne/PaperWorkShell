"""Live evidence for the production AppContainer temporary-storage path."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from secrets import token_hex
from uuid import uuid4

import pytest

from paperworkshell.worker.client._windows import (
    WindowsLaunchConfig,
    WindowsProcessLauncher,
)
from paperworkshell.worker.client.process import (
    TransportReceiveKind,
    WorkerLaunchRequest,
    WorkerResourcePolicy,
)
from paperworkshell.worker.protocol.models import SessionCapability
from tests.worker_resources import WorkerTestResources

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="PWS-0107 containment targets the supported Windows platform"
)


def _launch_config(resources: WorkerTestResources) -> WindowsLaunchConfig:
    return WindowsLaunchConfig(
        python_executable=resources.python_executable,
        bootstrap_script=resources.helper_root / "app_container_temp_probe.py",
        runtime_roots=(resources.runtime_root,),
        code_roots=(resources.source_root, resources.helper_root),
        working_directory=resources.helper_root,
    )


def _launch_request() -> WorkerLaunchRequest:
    return WorkerLaunchRequest(
        case_id=uuid4(),
        session_id=uuid4(),
        worker_instance_id=uuid4(),
        session_capability=SessionCapability(token_hex(32)),
        resource_policy=WorkerResourcePolicy(
            process_memory_bytes=256 * 1024 * 1024,
            job_memory_bytes=512 * 1024 * 1024,
            cpu_time_ms=30_000,
            maximum_processes=1,
        ),
    )


def _assert_profile_removed(profile_folder: Path) -> None:
    deadline = time.monotonic() + 1.0
    absent_since: float | None = None
    while time.monotonic() < deadline:
        now = time.monotonic()
        if profile_folder.exists():
            absent_since = None
        elif absent_since is None:
            absent_since = now
        elif now - absent_since >= 0.25:
            return
        time.sleep(0.025)
    assert not profile_folder.exists()


def test_live_worker_can_traverse_list_and_write_its_profile_temp(
    worker_test_resources: WorkerTestResources,
) -> None:
    process = WindowsProcessLauncher(_launch_config(worker_test_resources)).launch(
        _launch_request()
    )
    profile_folder = process._profile.local_app_data
    try:
        process.transport.send_payload(b"probe")
        received = process.transport.receive_payload(10.0)
        assert received.kind is TransportReceiveKind.PAYLOAD
        assert received.payload is not None
        response = json.loads(received.payload)
    finally:
        process.close()

    _assert_profile_removed(profile_folder)
    success = {"errno": 0, "ok": True, "winerror": 0}
    assert response["local_name"] == "AC"
    assert response["temp_name"] == "Temp"
    assert response["temp_parent_matches_local"] is True
    assert set(response["initial_local_stat"]) == {"errno", "ok", "winerror"}
    assert set(response["initial_temp_stat"]) == {"errno", "ok", "winerror"}
    assert response["materialize"] == success
    assert response["materialized_local_is_dir"] is True
    assert response["materialized_temp_is_dir"] is True
    assert response["materialized_local_stat"] == success
    assert response["materialized_local_list"] == success
    assert response["materialized_temp_stat"] == success
    assert response["materialized_temp_list"] == success
    assert response["temp_round_trip"] == success

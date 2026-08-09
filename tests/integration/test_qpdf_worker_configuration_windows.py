"""PWS-0112 live AppContainer evidence for a trusted fixed qpdf resource."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from secrets import token_hex
from typing import Any
from uuid import uuid4

import pytest

from paperworkshell.pdf.qpdf_checker import DEFAULT_QPDF_EXECUTABLE
from paperworkshell.worker.client import _windows as windows_launcher
from paperworkshell.worker.client._windows import (
    WindowsLaunchConfig,
    WindowsProcessLauncher,
    _AccessGrantLease,
)
from paperworkshell.worker.client.process import (
    TransportReceiveKind,
    WorkerLaunchRequest,
    WorkerResourcePolicy,
)
from paperworkshell.worker.protocol.models import SessionCapability
from tests.worker_resources import WorkerTestResources

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="PWS-0112 qpdf launch evidence targets the supported Windows platform",
)


def _qpdf_executable() -> Path:
    configured = os.environ.get("PWS_QPDF_EXECUTABLE")
    selected = Path(configured) if configured else DEFAULT_QPDF_EXECUTABLE
    assert selected.is_absolute() and selected.is_file(), "required qpdf 12.3.2 is unavailable"
    return selected


def _launch_config(
    resources: WorkerTestResources,
    qpdf_executable: Path,
) -> WindowsLaunchConfig:
    return WindowsLaunchConfig(
        python_executable=resources.python_executable,
        bootstrap_script=resources.helper_root / "qpdf_appcontainer_probe.py",
        runtime_roots=(resources.runtime_root,),
        code_roots=(resources.source_root, resources.helper_root),
        working_directory=resources.helper_root,
        qpdf_executable=qpdf_executable,
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
            maximum_processes=2,
        ),
    )


def test_installed_qpdf_runs_without_mutating_its_existing_acl(
    monkeypatch: pytest.MonkeyPatch,
    worker_test_resources: WorkerTestResources,
) -> None:
    qpdf_executable = _qpdf_executable()
    original_grant = _AccessGrantLease.grant_all

    def guard_grant(lease: _AccessGrantLease, paths: tuple[Path, ...]) -> None:
        assert qpdf_executable.parent not in paths
        original_grant(lease, paths)

    monkeypatch.setattr(_AccessGrantLease, "grant_all", guard_grant)
    process = WindowsProcessLauncher(_launch_config(worker_test_resources, qpdf_executable)).launch(
        _launch_request()
    )
    try:
        received = process.transport.receive_payload(20.0)
        assert received.kind is TransportReceiveKind.PAYLOAD
        assert received.payload is not None
        result: dict[str, Any] = json.loads(received.payload)
        assert process.wait(20.0) == 0
    finally:
        process.close()

    assert result == {
        "app_container": True,
        "configured": True,
        "in_job": True,
        "minimal_environment": True,
        "started": True,
        "status": 0,
        "version_match": True,
    }
    assert os.fspath(qpdf_executable) not in repr(process)
    assert windows_launcher._PROCESS_CREATION_QUARANTINED is False


def test_runner_temp_qpdf_uses_and_restores_temporary_access(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    worker_test_resources: WorkerTestResources,
) -> None:
    installed_qpdf = _qpdf_executable()
    qpdf_root = tmp_path / "runner-qpdf"
    shutil.copytree(installed_qpdf.parent, qpdf_root, copy_function=shutil.copyfile)
    qpdf_executable = qpdf_root / installed_qpdf.name
    original_grant = _AccessGrantLease.grant_all
    qpdf_grants = 0

    def capture_grant(lease: _AccessGrantLease, paths: tuple[Path, ...]) -> None:
        nonlocal qpdf_grants
        if qpdf_executable.parent in paths:
            qpdf_grants += 1
        original_grant(lease, paths)

    monkeypatch.setattr(_AccessGrantLease, "grant_all", capture_grant)
    process = WindowsProcessLauncher(_launch_config(worker_test_resources, qpdf_executable)).launch(
        _launch_request()
    )
    try:
        received = process.transport.receive_payload(20.0)
        assert received.kind is TransportReceiveKind.PAYLOAD
        assert received.payload is not None
        result: dict[str, Any] = json.loads(received.payload)
        assert process.wait(20.0) == 0
    finally:
        process.close()

    assert result["started"] is True
    assert result["version_match"] is True
    assert result["status"] == 0
    assert qpdf_grants == 1
    assert windows_launcher._PROCESS_CREATION_QUARANTINED is False


def test_two_live_profiles_keep_temp_qpdf_inside_recovery_allowlist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    worker_test_resources: WorkerTestResources,
) -> None:
    installed_qpdf = _qpdf_executable()
    qpdf_root = tmp_path / "runner-qpdf"
    shutil.copytree(installed_qpdf.parent, qpdf_root, copy_function=shutil.copyfile)
    qpdf_executable = qpdf_root / installed_qpdf.name
    recovery_root = tmp_path / "worker-acl-recovery"
    monkeypatch.setattr(
        windows_launcher,
        "_acl_recovery_directory_path",
        lambda: recovery_root,
    )
    launcher = WindowsProcessLauncher(_launch_config(worker_test_resources, qpdf_executable))
    first = launcher.launch(_launch_request())
    second = None
    try:
        first_received = first.transport.receive_payload(20.0)
        assert first_received.kind is TransportReceiveKind.PAYLOAD
        assert first.wait(20.0) == 0

        second = launcher.launch(_launch_request())
        second_received = second.transport.receive_payload(20.0)
        assert second_received.kind is TransportReceiveKind.PAYLOAD
        assert second.wait(20.0) == 0
    finally:
        if second is not None:
            second.close()
        first.close()

    assert windows_launcher._PROCESS_CREATION_QUARANTINED is False

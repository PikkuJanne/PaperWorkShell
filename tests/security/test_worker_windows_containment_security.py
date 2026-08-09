"""OS-enforced AppContainer, egress, environment, and filesystem evidence."""

from __future__ import annotations

import json
import os
import socket
from contextlib import ExitStack
from secrets import token_hex
from typing import Any
from uuid import uuid4

import pytest

from paperworkshell.worker.client._windows import WindowsLaunchConfig, WindowsProcessLauncher
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

_PROBE_PORTS = (46371, 46372, 46373, 46374, 46375)


def _launch_config(resources: WorkerTestResources) -> WindowsLaunchConfig:
    return WindowsLaunchConfig(
        python_executable=resources.python_executable,
        bootstrap_script=resources.helper_root / "worker_containment_probe.py",
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
            maximum_processes=4,
        ),
    )


def _bound_listeners(stack: ExitStack) -> tuple[socket.socket, ...]:
    for port in _PROBE_PORTS:
        sockets: list[socket.socket] = []
        try:
            tcp4 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sockets.append(tcp4)
            tcp4.bind(("127.0.0.1", port))
            tcp4.listen()

            udp4 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sockets.append(udp4)
            udp4.bind(("127.0.0.1", port))

            tcp6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            sockets.append(tcp6)
            tcp6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            tcp6.bind(("::1", port))
            tcp6.listen()

            udp6 = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
            sockets.append(udp6)
            udp6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            udp6.bind(("::1", port))
        except OSError:
            for candidate in sockets:
                candidate.close()
            continue
        for candidate in sockets:
            candidate.settimeout(1.0)
            stack.callback(candidate.close)
        return tcp4, udp4, tcp6, udp6
    raise AssertionError("the fixed local egress probe ports are unavailable")


def _prove_control_reachability(listeners: tuple[socket.socket, ...]) -> None:
    tcp4, udp4, tcp6, udp6 = listeners
    port = tcp4.getsockname()[1]
    with socket.create_connection(("127.0.0.1", port), timeout=1.0):
        accepted, _address = tcp4.accept()
        accepted.close()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        sender.sendto(b"control", ("127.0.0.1", port))
    assert udp4.recvfrom(64)[0] == b"control"

    with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sender:
        sender.settimeout(1.0)
        sender.connect(("::1", port))
        accepted, _address = tcp6.accept()
        accepted.close()
    with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as sender:
        sender.sendto(b"control", ("::1", port))
    assert udp6.recvfrom(64)[0] == b"control"


def _assert_no_worker_traffic(listeners: tuple[socket.socket, ...]) -> None:
    for listener in listeners:
        listener.settimeout(0.2)
        with pytest.raises(TimeoutError):
            if listener.type & socket.SOCK_DGRAM:
                listener.recvfrom(64)
            else:
                listener.accept()


def test_zero_capability_worker_and_descendant_have_no_egress_or_broad_file_access(
    monkeypatch: pytest.MonkeyPatch,
    worker_test_resources: WorkerTestResources,
) -> None:
    for name in (
        "PATH",
        "PATHEXT",
        "PYTHONPATH",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "AWS_SECRET_ACCESS_KEY",
        "AZURE_CLIENT_SECRET",
        "COMSPEC",
    ):
        monkeypatch.setenv(name, "ambient-canary-must-not-cross-worker-boundary")
    outside_write = worker_test_resources.repository_root / ".pws-outside-write-probe"
    approved_write = worker_test_resources.helper_root / ".pws-approved-write-probe"
    assert not outside_write.exists()
    assert not approved_write.exists()

    with ExitStack() as stack:
        listeners = _bound_listeners(stack)
        _prove_control_reachability(listeners)
        process = WindowsProcessLauncher(_launch_config(worker_test_resources)).launch(
            _launch_request()
        )
        profile_folder = process._profile.local_app_data
        try:
            received = process.transport.receive_payload(15.0)
            assert received.kind is TransportReceiveKind.PAYLOAD
            assert received.payload is not None
            result: dict[str, Any] = json.loads(received.payload)
            assert process.wait(15.0) == 0
        finally:
            process.close()
        _assert_no_worker_traffic(listeners)

    assert result["app_container"] is True
    assert result["zero_capabilities"] is True
    assert result["in_job"] is True
    assert result["minimal_environment"] is True
    assert result["unexpected_environment"] is False
    assert result["tcp4_connected"] is False
    assert result["tcp6_connected"] is False
    assert result["approved_source_read"] is True
    assert result["approved_source_write"] is False
    assert result["outside_source_read"] is False
    assert result["outside_source_write"] is False
    descendant = result["descendant"]
    assert type(descendant) is dict
    assert descendant["failure_stage"] == 0
    assert descendant["completed"] is True
    assert descendant["tcp4_connected"] is False
    assert descendant["tcp6_connected"] is False
    assert not profile_folder.exists()
    assert not outside_write.exists()
    assert not approved_write.exists()

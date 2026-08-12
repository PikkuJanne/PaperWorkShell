"""Focused GUI-safe worker resource telemetry tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest

from paperworkshell.worker.client.client import WorkerClient
from paperworkshell.worker.client.errors import (
    WorkerClientContext,
    WorkerClientError,
    WorkerClientErrorCode,
)
from paperworkshell.worker.client.models import (
    WorkerResourceSnapshot,
    WorkerTimeoutPolicy,
)
from paperworkshell.worker.client.process import (
    BrokeredHandlePurpose,
    IsolationEvidence,
    TransportReceive,
    TransportReceiveKind,
    WorkerLaunchRequest,
    WorkerResourcePolicy,
)


def _resource_policy() -> WorkerResourcePolicy:
    return WorkerResourcePolicy(
        process_memory_bytes=64 * 1024 * 1024,
        job_memory_bytes=128 * 1024 * 1024,
        cpu_time_ms=20_000,
        maximum_processes=2,
    )


def _snapshot(*, process_peak: int, job_peak: int) -> WorkerResourceSnapshot:
    policy = _resource_policy()
    return WorkerResourceSnapshot(
        process_memory_limit_bytes=policy.process_memory_bytes,
        job_memory_limit_bytes=policy.job_memory_bytes,
        peak_process_memory_used_bytes=process_peak,
        peak_job_memory_used_bytes=job_peak,
    )


class _Transport:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def send_payload(self, _payload: bytes) -> None:
        return None

    def receive_payload(self, _timeout_seconds: float) -> TransportReceive:
        return TransportReceive(TransportReceiveKind.TIMED_OUT)

    def close(self) -> None:
        self._events.append("transport.close")


class _Process:
    def __init__(self, initial: WorkerResourceSnapshot) -> None:
        self.pid = 4_114
        self.events: list[str] = []
        self.transport = _Transport(self.events)
        self.isolation = IsolationEvidence(
            app_container=True,
            network_denied=True,
            job_object_assigned=True,
            kill_on_job_close=True,
            case_paths_absent=True,
            minimal_environment=True,
            explicit_handle_list=True,
            shell_disabled=True,
            inherited_handle_count=3,
        )
        self.observed: WorkerResourceSnapshot | object = initial
        self.raise_on_observation = False

    def poll(self) -> int | None:
        return None

    def wait(self, _timeout_seconds: float | None) -> int | None:
        return None

    def duplicate_handle(
        self,
        source_handle: int,
        _purpose: BrokeredHandlePurpose,
    ) -> int:
        return source_handle + 1

    def terminate_tree(self) -> None:
        self.events.append("process.terminate")

    def close(self) -> None:
        self.events.append("process.close")

    def resource_snapshot(self) -> WorkerResourceSnapshot:
        self.events.append("process.resource_snapshot")
        if self.raise_on_observation:
            raise OSError("C:/private/applicant-resource-canary.pdf")
        return self.observed  # type: ignore[return-value]


class _Launcher:
    def __init__(self, process: _Process) -> None:
        self.process = process

    def launch(self, _request: WorkerLaunchRequest) -> _Process:
        return self.process


def _client(process: _Process) -> WorkerClient:
    return WorkerClient(
        _Launcher(process),
        _resource_policy(),
        WorkerTimeoutPolicy(
            handshake_seconds=2.0,
            cancellation_grace_seconds=1.0,
            shutdown_grace_seconds=1.0,
        ),
    )


@pytest.mark.parametrize(
    "values",
    (
        (0, 2, 0, 0),
        (1, 0, 0, 0),
        (2, 1, 0, 0),
        (2, 2, 3, 3),
        (2, 2, 1, 0),
        (True, 2, 0, 0),
    ),
)
def test_resource_snapshot_rejects_invalid_or_impossible_numbers(
    values: tuple[object, object, object, object],
) -> None:
    with pytest.raises(WorkerClientError) as raised:
        WorkerResourceSnapshot(*values)  # type: ignore[arg-type]

    assert raised.value.code is WorkerClientErrorCode.RESOURCE_REPORT_FAILED
    assert raised.value.context is WorkerClientContext.RESOURCE


def test_resource_snapshot_is_immutable_and_monotonic_contract_is_exact() -> None:
    earlier = WorkerResourceSnapshot(10, 20, 2, 4)
    later = WorkerResourceSnapshot(10, 20, 3, 5)

    assert later.is_monotonic_successor_of(earlier)
    assert not earlier.is_monotonic_successor_of(later)
    assert "path" not in repr(later).casefold()
    with pytest.raises(FrozenInstanceError):
        later.peak_job_memory_used_bytes = 6  # type: ignore[misc]


def test_client_caches_a_terminal_snapshot_before_process_authority_closes() -> None:
    initial = _snapshot(process_peak=1_000, job_peak=2_000)
    terminal = _snapshot(process_peak=3_000, job_peak=5_000)
    process = _Process(initial)
    client = _client(process)
    client.start(uuid4())

    assert client.resource_snapshot() == initial
    process.observed = terminal
    client.terminate()

    assert client.query().last_resource_snapshot == terminal
    assert process.events[-4:] == [
        "process.terminate",
        "process.resource_snapshot",
        "process.close",
        "transport.close",
    ]


def test_client_rejects_regressing_or_wrong_limit_reports_and_keeps_last_good_cache() -> None:
    initial = _snapshot(process_peak=3_000, job_peak=5_000)
    process = _Process(initial)
    client = _client(process)
    client.start(uuid4())
    assert client.resource_snapshot() == initial

    process.observed = _snapshot(process_peak=2_999, job_peak=5_000)
    with pytest.raises(WorkerClientError) as regressed:
        client.resource_snapshot()
    process.observed = WorkerResourceSnapshot(
        process_memory_limit_bytes=32 * 1024 * 1024,
        job_memory_limit_bytes=128 * 1024 * 1024,
        peak_process_memory_used_bytes=2_000,
        peak_job_memory_used_bytes=4_000,
    )
    with pytest.raises(WorkerClientError) as wrong_limit:
        client.resource_snapshot()

    assert regressed.value.code is WorkerClientErrorCode.RESOURCE_REPORT_FAILED
    assert wrong_limit.value.code is WorkerClientErrorCode.RESOURCE_REPORT_FAILED
    assert client.query().last_resource_snapshot == initial
    client.terminate()


def test_resource_reporter_exception_is_sanitized_and_does_not_replace_cache() -> None:
    initial = _snapshot(process_peak=1_000, job_peak=2_000)
    process = _Process(initial)
    client = _client(process)
    client.start(uuid4())
    assert client.resource_snapshot() == initial
    process.raise_on_observation = True

    with pytest.raises(WorkerClientError) as raised:
        client.resource_snapshot()

    assert raised.value.code is WorkerClientErrorCode.RESOURCE_REPORT_FAILED
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert "applicant" not in str(raised.value).casefold()
    assert client.query().last_resource_snapshot == initial
    client.terminate()

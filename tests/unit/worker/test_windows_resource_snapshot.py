"""Safe Windows Job resource-accounting contract tests."""

from __future__ import annotations

import ctypes
from dataclasses import FrozenInstanceError
from typing import Any, cast

import pytest

from paperworkshell.worker.client import _windows as windows_module
from paperworkshell.worker.client._windows import (
    WindowsLaunchError,
    WindowsLaunchErrorCode,
    WindowsWorkerProcess,
    WindowsWorkerResourceSnapshot,
    _Job,
    _OwnedHandle,
)
from paperworkshell.worker.client.models import WorkerResourceSnapshot


class _Function:
    def __init__(self, callback: Any) -> None:
        self._callback = callback
        self.argtypes: object = None
        self.restype: object = None

    def __call__(self, *args: object) -> object:
        return self._callback(*args)


class _FakeKernel32:
    def __init__(
        self,
        *,
        succeed: bool = True,
        returned_length: int | None = None,
        limit_flags: int | None = None,
    ) -> None:
        self.succeed = succeed
        self.returned_length = returned_length
        self.limit_flags = (
            windows_module._JOB_OBJECT_LIMIT_PROCESS_MEMORY
            | windows_module._JOB_OBJECT_LIMIT_JOB_MEMORY
            | windows_module._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if limit_flags is None
            else limit_flags
        )
        self.calls: list[tuple[object, ...]] = []
        self.QueryInformationJobObject = _Function(self._query_information_job)

    def _query_information_job(self, *args: object) -> bool:
        self.calls.append(args)
        if not self.succeed:
            return False
        information = cast(Any, args[2])._obj
        information.basic_limit_information.limit_flags = self.limit_flags
        information.process_memory_limit = 256 * 1024 * 1024
        information.job_memory_limit = 512 * 1024 * 1024
        information.peak_process_memory_used = 72 * 1024 * 1024
        information.peak_job_memory_used = 96 * 1024 * 1024
        returned_length = cast(Any, args[4])._obj
        assert type(args[3]) is int
        returned_length.value = args[3] if self.returned_length is None else self.returned_length
        return True


def _worker_process() -> WindowsWorkerProcess:
    job = cast(Any, _Job.__new__(_Job))
    job.handle = _OwnedHandle(7_654_321)
    process = cast(Any, WindowsWorkerProcess.__new__(WindowsWorkerProcess))
    process._process = _OwnedHandle(1_234_567)
    process._job = job
    process._closed = False
    process._containment_closed = False
    return cast(WindowsWorkerProcess, process)


def test_resource_snapshot_returns_only_immutable_authoritative_numbers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel32 = _FakeKernel32()
    monkeypatch.setattr(windows_module, "_kernel32", lambda: kernel32)
    worker = _worker_process()

    snapshot = worker.resource_snapshot()

    assert WindowsWorkerResourceSnapshot is WorkerResourceSnapshot
    assert snapshot == WindowsWorkerResourceSnapshot(
        process_memory_limit_bytes=256 * 1024 * 1024,
        job_memory_limit_bytes=512 * 1024 * 1024,
        peak_process_memory_used_bytes=72 * 1024 * 1024,
        peak_job_memory_used_bytes=96 * 1024 * 1024,
    )
    assert len(kernel32.calls) == 1
    call = kernel32.calls[0]
    assert cast(Any, call[0]).value == 7_654_321
    assert call[1] == windows_module._JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS
    assert call[3] == ctypes.sizeof(windows_module._JobObjectExtendedLimitInformation)
    assert set(snapshot.__dataclass_fields__) == {
        "process_memory_limit_bytes",
        "job_memory_limit_bytes",
        "peak_process_memory_used_bytes",
        "peak_job_memory_used_bytes",
    }
    assert "1234567" not in repr(snapshot)
    assert "7654321" not in repr(snapshot)
    with pytest.raises(FrozenInstanceError):
        snapshot.peak_job_memory_used_bytes = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    "kernel32",
    (
        _FakeKernel32(succeed=False),
        _FakeKernel32(returned_length=0),
        _FakeKernel32(limit_flags=windows_module._JOB_OBJECT_LIMIT_JOB_MEMORY),
    ),
)
def test_resource_snapshot_rejects_query_or_contract_failures_without_losing_authority(
    monkeypatch: pytest.MonkeyPatch,
    kernel32: _FakeKernel32,
) -> None:
    monkeypatch.setattr(windows_module, "_kernel32", lambda: kernel32)
    worker = _worker_process()

    with pytest.raises(WindowsLaunchError) as raised:
        worker.resource_snapshot()

    assert raised.value.code is WindowsLaunchErrorCode.JOB_FAILED
    assert raised.value.context == "job.resource_snapshot"
    assert "1234567" not in str(raised.value)
    assert "7654321" not in str(raised.value)
    assert cast(Any, worker)._process.is_open
    assert cast(Any, worker)._job.handle.is_open


def test_resource_snapshot_rejects_closed_authority_before_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel32 = _FakeKernel32()
    monkeypatch.setattr(windows_module, "_kernel32", lambda: kernel32)
    worker = _worker_process()
    cast(Any, worker)._containment_closed = True

    with pytest.raises(WindowsLaunchError) as raised:
        worker.resource_snapshot()

    assert raised.value.code is WindowsLaunchErrorCode.PROCESS_FAILED
    assert raised.value.context == "resource_snapshot.closed"
    assert kernel32.calls == []
    assert "1234567" not in str(raised.value)
    assert "7654321" not in str(raised.value)

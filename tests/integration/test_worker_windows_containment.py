"""Windows integration evidence for the restricted PWS-0107 worker launch."""

from __future__ import annotations

import ast
import ctypes
import os
import sys
import threading
import time
import traceback
import venv
from collections.abc import Iterator
from ctypes import wintypes
from pathlib import Path
from secrets import token_hex
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock
from uuid import uuid4

import pytest

from paperworkshell.worker.client import _windows as windows_module
from paperworkshell.worker.client._acl_recovery import (
    AclRecoveryEntry,
    AclRecoveryJournal,
    AclRecoveryJournalError,
    AclRecoveryPhase,
)
from paperworkshell.worker.client._windows import (
    WindowsLaunchConfig,
    WindowsLaunchError,
    WindowsLaunchErrorCode,
    WindowsProcessLauncher,
    WindowsWorkerProcess,
    _AclMutationMutex,
    _AppContainerProfile,
    _Job,
    _OwnedHandle,
    _PipeSet,
    _WindowsPipeReader,
    _WindowsPipeWriter,
)
from paperworkshell.worker.client.client import WorkerClient
from paperworkshell.worker.client.models import (
    WorkerClientSnapshot,
    WorkerLifecyclePhase,
    WorkerTimeoutPolicy,
)
from paperworkshell.worker.client.process import (
    IsolationEvidence,
    TransportReceiveKind,
    WorkerLaunchRequest,
    WorkerProcess,
    WorkerResourcePolicy,
)
from paperworkshell.worker.protocol.models import (
    NOOP_CAPABILITY,
    Direction,
    ErrorCode,
    MessageType,
    OperationOutcome,
    OperationResultPayload,
    OperationStartPayload,
    ProtocolErrorPayload,
    ResourceLimits,
    SessionCapability,
    SessionClosedPayload,
    SessionHelloPayload,
    SessionReadyPayload,
    SessionShutdownPayload,
    ShutdownReason,
)
from paperworkshell.worker.protocol.peer import PeerBinding, ProtocolPeer
from paperworkshell.worker.protocol.state import ConversationPhase
from paperworkshell.worker.runtime import HostExitCode
from tests.worker_resources import WorkerTestResources

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="PWS-0107 containment targets the supported Windows platform"
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_SE_FILE_OBJECT = 1
_DACL_SECURITY_INFORMATION = 0x0000_0004
_SDDL_REVISION_1 = 1


def _dacl_sddl(path: Path) -> str:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_security_info = advapi32.GetNamedSecurityInfoW
    get_security_info.argtypes = (
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    )
    get_security_info.restype = wintypes.DWORD
    convert = advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW
    convert.argtypes = (
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(wintypes.DWORD),
    )
    convert.restype = wintypes.BOOL
    local_free = kernel32.LocalFree
    local_free.argtypes = (ctypes.c_void_p,)
    local_free.restype = ctypes.c_void_p
    descriptor = ctypes.c_void_p()
    rendered = wintypes.LPWSTR()
    status = get_security_info(
        ctypes.create_unicode_buffer(os.fspath(path)),
        _SE_FILE_OBJECT,
        _DACL_SECURITY_INFORMATION,
        None,
        None,
        None,
        None,
        ctypes.byref(descriptor),
    )
    assert status == 0
    try:
        assert convert(
            descriptor,
            _SDDL_REVISION_1,
            _DACL_SECURITY_INFORMATION,
            ctypes.byref(rendered),
            None,
        )
        assert rendered.value is not None
        return rendered.value
    finally:
        local_free(rendered)
        local_free(descriptor)


def _set_null_dacl(path: Path) -> None:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    set_security_info = advapi32.SetNamedSecurityInfoW
    set_security_info.argtypes = (
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    )
    set_security_info.restype = wintypes.DWORD
    mutable_path = ctypes.create_unicode_buffer(os.fspath(path))
    assert (
        set_security_info(
            mutable_path,
            _SE_FILE_OBJECT,
            _DACL_SECURITY_INFORMATION,
            None,
            None,
            None,
            None,
        )
        == 0
    )


def _dacl_is_null(path: Path) -> bool:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_security_info = advapi32.GetNamedSecurityInfoW
    get_security_info.argtypes = (
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    )
    get_security_info.restype = wintypes.DWORD
    local_free = kernel32.LocalFree
    local_free.argtypes = (ctypes.c_void_p,)
    local_free.restype = ctypes.c_void_p
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    mutable_path = ctypes.create_unicode_buffer(os.fspath(path))
    status = get_security_info(
        mutable_path,
        _SE_FILE_OBJECT,
        _DACL_SECURITY_INFORMATION,
        None,
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    assert status == 0
    try:
        return not dacl.value
    finally:
        local_free(descriptor)


def _restore_dacl_sddl(path: Path, sddl: str) -> None:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    convert = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    )
    convert.restype = wintypes.BOOL
    set_security = advapi32.SetFileSecurityW
    set_security.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p)
    set_security.restype = wintypes.BOOL
    local_free = kernel32.LocalFree
    local_free.argtypes = (ctypes.c_void_p,)
    local_free.restype = ctypes.c_void_p
    descriptor = ctypes.c_void_p()
    assert convert(sddl, _SDDL_REVISION_1, ctypes.byref(descriptor), None)
    try:
        assert set_security(os.fspath(path), _DACL_SECURITY_INFORMATION, descriptor)
    finally:
        local_free(descriptor)


def _launch_config(resources: WorkerTestResources) -> WindowsLaunchConfig:
    source_root = resources.source_root
    return WindowsLaunchConfig(
        python_executable=resources.python_executable,
        bootstrap_script=source_root / "paperworkshell" / "worker" / "host_bootstrap.py",
        runtime_roots=(resources.runtime_root,),
        code_roots=(source_root,),
        working_directory=source_root,
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


def _isolation_evidence() -> IsolationEvidence:
    return IsolationEvidence(
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


def _noop_operation(*, wall_time_ms: int = 5_000) -> OperationStartPayload:
    return OperationStartPayload(
        "protocol.noop",
        1,
        (),
        (),
        ResourceLimits(
            wall_time_ms=wall_time_ms,
            cpu_time_ms=5_000,
            memory_bytes=128 * 1024 * 1024,
            temporary_storage_bytes=1024,
            output_bytes=1024,
            page_count=1,
            pixel_count=1,
        ),
    )


def _pump_until(
    client: WorkerClient,
    phase: WorkerLifecyclePhase,
    *,
    timeout_seconds: float = 15.0,
) -> tuple[bool, WorkerClientSnapshot]:
    deadline = time.monotonic() + timeout_seconds
    observed_progress = False
    snapshot = client.query()
    while snapshot.phase is not phase and time.monotonic() < deadline:
        snapshot = client.pump(min(0.25, max(0.0, deadline - time.monotonic())))
        if snapshot.active_operation is not None and snapshot.active_operation.progress is not None:
            observed_progress = True
    assert snapshot.phase is phase
    return observed_progress, snapshot


def _negotiate(process: WorkerProcess, request: WorkerLaunchRequest) -> ProtocolPeer:
    peer = ProtocolPeer(
        PeerBinding(
            request.session_id,
            request.worker_instance_id,
            request.case_id,
            request.session_capability,
        ),
        Direction.CLIENT_TO_WORKER,
    )
    hello_request = uuid4()
    hello = peer.prepare_outbound(
        MessageType.SESSION_HELLO,
        SessionHelloPayload((1,), (NOOP_CAPABILITY,)),
        request_id=hello_request,
        operation_id=None,
    )
    process.transport.send_payload(hello.encoded)
    received = process.transport.receive_payload(10.0)
    assert received.kind is TransportReceiveKind.PAYLOAD
    assert received.payload is not None
    ready = peer.accept_inbound(received.payload)
    assert ready.message_type is MessageType.SESSION_READY
    assert type(ready.payload) is SessionReadyPayload
    assert ready.request_id == hello_request
    return peer


def test_live_worker_resource_snapshot_reports_applied_limits_and_closes_cleanly(
    worker_test_resources: WorkerTestResources,
) -> None:
    request = _launch_request()
    process = WindowsProcessLauncher(_launch_config(worker_test_resources)).launch(request)
    profile_folder = process._profile.local_app_data
    try:
        deadline = time.monotonic() + 5.0
        snapshot = process.resource_snapshot()
        while snapshot.peak_job_memory_used_bytes == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
            snapshot = process.resource_snapshot()

        assert snapshot.process_memory_limit_bytes == request.resource_policy.process_memory_bytes
        assert snapshot.job_memory_limit_bytes == request.resource_policy.job_memory_bytes
        assert snapshot.peak_process_memory_used_bytes > 0
        assert snapshot.peak_job_memory_used_bytes >= snapshot.peak_process_memory_used_bytes

        process.terminate_tree()
        terminated_snapshot = process.resource_snapshot()
        assert terminated_snapshot.process_memory_limit_bytes == snapshot.process_memory_limit_bytes
        assert terminated_snapshot.job_memory_limit_bytes == snapshot.job_memory_limit_bytes
        assert terminated_snapshot.peak_job_memory_used_bytes >= snapshot.peak_job_memory_used_bytes
    finally:
        process.close()

    _assert_profile_removed(profile_folder)
    with pytest.raises(WindowsLaunchError) as raised:
        process.resource_snapshot()
    assert raised.value.code is WindowsLaunchErrorCode.PROCESS_FAILED
    assert raised.value.context == "resource_snapshot.closed"


def test_actual_python_host_negotiates_and_closes_inside_app_container(
    worker_test_resources: WorkerTestResources,
) -> None:
    request = _launch_request()
    process = WindowsProcessLauncher(_launch_config(worker_test_resources)).launch(request)
    profile_folder = process._profile.local_app_data
    try:
        assert process.isolation.satisfies_required_boundary
        assert process.isolation.inherited_handle_count == 3
        peer = _negotiate(process, request)

        operation_request = uuid4()
        operation_id = uuid4()
        start = peer.prepare_outbound(
            MessageType.OPERATION_START,
            _noop_operation(),
            request_id=operation_request,
            operation_id=operation_id,
        )
        process.transport.send_payload(start.encoded)
        result: OperationResultPayload | None = None
        for _ in range(4):
            received = process.transport.receive_payload(10.0)
            assert received.kind is TransportReceiveKind.PAYLOAD
            assert received.payload is not None
            message = peer.accept_inbound(received.payload)
            if type(message.payload) is OperationResultPayload:
                result = message.payload
                break
        assert result is not None
        assert result.outcome is OperationOutcome.SUCCEEDED

        shutdown_request = uuid4()
        shutdown = peer.prepare_outbound(
            MessageType.SESSION_SHUTDOWN,
            SessionShutdownPayload(ShutdownReason.APPLICATION_EXIT),
            request_id=shutdown_request,
            operation_id=None,
        )
        process.transport.send_payload(shutdown.encoded)
        received = process.transport.receive_payload(10.0)
        assert received.kind is TransportReceiveKind.PAYLOAD
        assert received.payload is not None
        closed = peer.accept_inbound(received.payload)
        assert closed.message_type is MessageType.SESSION_CLOSED
        assert type(closed.payload) is SessionClosedPayload
        assert closed.request_id == shutdown_request
        assert process.wait(10.0) == 0
    finally:
        process.close()
    _assert_profile_removed(profile_folder)


def test_base_interpreter_uses_consumed_venv_hint_in_one_process(tmp_path: Path) -> None:
    base_runtime = Path(sys.base_prefix).resolve()
    venv_runtime = tmp_path / "worker-venv"
    venv.EnvBuilder(with_pip=False).create(venv_runtime)
    source_root = _REPOSITORY_ROOT / "src"
    config = WindowsLaunchConfig(
        python_executable=(base_runtime / "python.exe").resolve(),
        bootstrap_script=(source_root / "paperworkshell" / "worker" / "host_bootstrap.py"),
        runtime_roots=(venv_runtime, base_runtime),
        code_roots=(source_root,),
        working_directory=source_root,
        venv_launcher=(venv_runtime / "Scripts" / "python.exe").resolve(),
    )
    policy = WorkerResourcePolicy(
        process_memory_bytes=512 * 1024 * 1024,
        job_memory_bytes=768 * 1024 * 1024,
        cpu_time_ms=30_000,
        maximum_processes=1,
    )
    client = WorkerClient(
        WindowsProcessLauncher(config),
        policy,
        WorkerTimeoutPolicy(
            handshake_seconds=30.0,
            cancellation_grace_seconds=5.0,
            shutdown_grace_seconds=10.0,
        ),
        required_capabilities=(NOOP_CAPABILITY,),
    )

    try:
        client.start(uuid4())
        _, ready = _pump_until(client, WorkerLifecyclePhase.READY, timeout_seconds=30.0)
        assert ready.last_failure is None
        assert ready.capabilities == (NOOP_CAPABILITY,)
        assert policy.maximum_processes == 1

        client.start_operation(_noop_operation())
        _, completed = _pump_until(client, WorkerLifecyclePhase.READY)
        assert completed.last_result is not None
        assert completed.last_result.outcome is OperationOutcome.SUCCEEDED
    finally:
        client.terminate()


def test_actual_python_host_timeout_requires_restart_and_exits(
    worker_test_resources: WorkerTestResources,
) -> None:
    request = _launch_request()
    process = WindowsProcessLauncher(_launch_config(worker_test_resources)).launch(request)
    profile_folder = process._profile.local_app_data
    try:
        peer = _negotiate(process, request)
        operation_request = uuid4()
        operation_id = uuid4()
        start = peer.prepare_outbound(
            MessageType.OPERATION_START,
            _noop_operation(wall_time_ms=1),
            request_id=operation_request,
            operation_id=operation_id,
        )
        process.transport.send_payload(start.encoded)

        result: OperationResultPayload | None = None
        for _ in range(5):
            received = process.transport.receive_payload(10.0)
            assert received.kind is TransportReceiveKind.PAYLOAD
            assert received.payload is not None
            message = peer.accept_inbound(received.payload)
            if type(message.payload) is OperationResultPayload:
                result = message.payload
                break

        assert result is not None
        assert result.outcome is OperationOutcome.TIMED_OUT
        assert result.error is not None
        assert result.error.code is ErrorCode.WORKER_OPERATION_TIMED_OUT
        assert result.error.restart_required
        assert peer.state.phase is ConversationPhase.FAULTED
        assert process.wait(10.0) == HostExitCode.OPERATION_TIMED_OUT
    finally:
        process.close()
    _assert_profile_removed(profile_folder)


def test_actual_python_host_malformed_rpc_fails_closed_with_protocol_error(
    worker_test_resources: WorkerTestResources,
) -> None:
    request = _launch_request()
    process = WindowsProcessLauncher(_launch_config(worker_test_resources)).launch(request)
    profile_folder = process._profile.local_app_data
    try:
        peer = _negotiate(process, request)
        process.transport.send_payload(b"{}")

        received = process.transport.receive_payload(10.0)
        assert received.kind is TransportReceiveKind.PAYLOAD
        assert received.payload is not None
        message = peer.accept_inbound(received.payload)
        assert message.message_type is MessageType.PROTOCOL_ERROR
        assert type(message.payload) is ProtocolErrorPayload
        assert message.payload.error.code is ErrorCode.RPC_MESSAGE_INVALID
        assert message.payload.error.restart_required
        assert peer.state.phase is ConversationPhase.FAULTED
        assert process.wait(10.0) == HostExitCode.PROTOCOL_FAULT
        assert process.transport.receive_payload(1.0).kind is TransportReceiveKind.CLOSED
    finally:
        process.close()
    _assert_profile_removed(profile_folder)


def test_worker_client_real_launch_operation_cancel_restart_and_shutdown(
    worker_test_resources: WorkerTestResources,
) -> None:
    client = WorkerClient(
        WindowsProcessLauncher(_launch_config(worker_test_resources)),
        _launch_request().resource_policy,
        WorkerTimeoutPolicy(
            handshake_seconds=15.0,
            cancellation_grace_seconds=5.0,
            shutdown_grace_seconds=10.0,
        ),
        required_capabilities=(NOOP_CAPABILITY,),
    )
    first_generation = client.start(uuid4())
    try:
        _, ready = _pump_until(client, WorkerLifecyclePhase.READY)
        first_session = ready.session_id
        first_worker = ready.worker_instance_id

        client.start_operation(_noop_operation())
        observed_progress, completed = _pump_until(client, WorkerLifecyclePhase.READY)
        assert observed_progress
        assert completed.last_result is not None
        assert completed.last_result.outcome is OperationOutcome.SUCCEEDED

        client.start_operation(_noop_operation())
        client.cancel()
        _, cancelled = _pump_until(client, WorkerLifecyclePhase.READY)
        assert cancelled.last_result is not None
        assert cancelled.last_result.outcome is OperationOutcome.CANCELLED

        second_generation = client.restart()
        assert second_generation == first_generation + 1
        _, restarted = _pump_until(client, WorkerLifecyclePhase.READY)
        assert restarted.session_id != first_session
        assert restarted.worker_instance_id != first_worker

        client.shutdown()
        _, stopped = _pump_until(client, WorkerLifecyclePhase.STOPPED)
        assert stopped.pid is None
    finally:
        client.terminate()


def test_force_termination_ends_the_retained_job_tree(
    worker_test_resources: WorkerTestResources,
) -> None:
    process = WindowsProcessLauncher(_launch_config(worker_test_resources)).launch(
        _launch_request()
    )
    try:
        assert process.poll() is None
        for invalid_timeout in (True, float("nan"), -1.0):
            with pytest.raises(WindowsLaunchError) as raised:
                process.wait(invalid_timeout)
            assert raised.value.code is WindowsLaunchErrorCode.CONFIGURATION_INVALID
            assert raised.value.context == "wait.timeout"
        process.terminate_tree()
        assert process.wait(10.0) is not None
        assert process.poll() is not None
    finally:
        process.close()


def test_fresh_launch_after_termination_uses_new_binding_and_negotiates(
    worker_test_resources: WorkerTestResources,
) -> None:
    first_request = _launch_request()
    first = WindowsProcessLauncher(_launch_config(worker_test_resources)).launch(first_request)
    _negotiate(first, first_request)
    first.terminate_tree()
    first.close()

    second_request = _launch_request()
    assert second_request.session_id != first_request.session_id
    assert second_request.worker_instance_id != first_request.worker_instance_id
    assert not second_request.session_capability.matches(first_request.session_capability)
    second = WindowsProcessLauncher(_launch_config(worker_test_resources)).launch(second_request)
    try:
        _negotiate(second, second_request)
        assert second.poll() is None
    finally:
        second.close()


def test_existing_descendant_access_is_temporary_and_dacl_equivalent_after_revoke(
    tmp_path: Path,
) -> None:
    approved = tmp_path / "approved-runtime"
    approved.mkdir()
    existing = approved / "existing-resource.bin"
    existing.write_bytes(b"synthetic-runtime-resource")
    before = (_dacl_sddl(approved), _dacl_sddl(existing))

    profile = _AppContainerProfile.create()
    profile_folder = profile.local_app_data
    profile.grant_read_execute((approved,))
    profile.require_read_execute((approved, existing))
    assert profile.close_no_raise()
    assert profile.close_no_raise()

    assert (_dacl_sddl(approved), _dacl_sddl(existing)) == before
    _assert_profile_removed(profile_folder)


def test_overlapping_access_leases_revoke_only_their_own_profile_sid(tmp_path: Path) -> None:
    approved = tmp_path / "shared-runtime"
    approved.mkdir()
    existing = approved / "existing-resource.bin"
    existing.write_bytes(b"synthetic-runtime-resource")
    before = (_dacl_sddl(approved), _dacl_sddl(existing))
    first = _AppContainerProfile.create()
    second = _AppContainerProfile.create()
    first_folder = first.local_app_data
    second_folder = second.local_app_data
    try:
        first.grant_read_execute((approved,))
        first.require_read_execute((approved, existing))
        second.grant_read_execute((approved,))
        second.require_read_execute((approved, existing))

        assert first.close_no_raise()
        second.require_read_execute((approved, existing))
        assert second.close_no_raise()
    finally:
        first.close_no_raise()
        second.close_no_raise()

    assert (_dacl_sddl(approved), _dacl_sddl(existing)) == before
    _assert_profile_removed(first_folder)
    _assert_profile_removed(second_folder)


def test_overlapping_hardlink_guards_preserve_preexisting_user_deny(
    tmp_path: Path,
) -> None:
    approved = tmp_path / "shared-guarded-runtime"
    approved.mkdir()
    existing = approved / "existing-resource.bin"
    alias = tmp_path / "existing-resource-alias.bin"
    existing.write_bytes(b"synthetic-runtime-resource")
    original_sddl = _dacl_sddl(existing)
    user_sid = windows_module._convert_string_sid(windows_module._current_process_user_sid_string())
    handle, _info = windows_module._open_retained_resource(
        existing,
        acl_authority=True,
    )
    original_auto_inherited = windows_module._dacl_auto_inherited_handle(handle.value)
    try:
        windows_module._append_exact_deny_ace_handle(
            handle.value,
            user_sid,
            windows_module._FILE_WRITE_ATTRIBUTES,
        )
        windows_module._restore_dacl_auto_inherited_handle(
            handle.value,
            original_auto_inherited,
        )
    finally:
        handle.close()
    preexisting_guard_sddl = _dacl_sddl(existing)
    first = _AppContainerProfile.create()
    second = _AppContainerProfile.create()
    first_folder = first.local_app_data
    second_folder = second.local_app_data
    try:
        first.grant_read_execute((approved,))
        second.grant_read_execute((approved,))
        first_inventory = first.access.inventory
        assert first_inventory is not None
        resource = first_inventory.resources_by_path[windows_module._normalized_path(existing)]
        assert (
            windows_module._exact_deny_ace_count_handle(
                resource.handle.value,
                user_sid,
                windows_module._FILE_WRITE_ATTRIBUTES,
            )
            == 2
        )
        with pytest.raises(PermissionError):
            os.link(existing, alias)

        assert first.close_no_raise()
        second_inventory = second.access.inventory
        assert second_inventory is not None
        resource = second_inventory.resources_by_path[windows_module._normalized_path(existing)]
        assert (
            windows_module._exact_deny_ace_count_handle(
                resource.handle.value,
                user_sid,
                windows_module._FILE_WRITE_ATTRIBUTES,
            )
            == 2
        )
        with pytest.raises(PermissionError):
            os.link(existing, alias)

        assert second.close_no_raise()
        assert _dacl_sddl(existing) == preexisting_guard_sddl
        with pytest.raises(PermissionError):
            os.link(existing, alias)
    finally:
        first.close_no_raise()
        second.close_no_raise()
        cleanup_handle, _cleanup_info = windows_module._open_retained_resource(
            existing,
            acl_authority=True,
        )
        try:
            windows_module._remove_one_exact_deny_ace_handle(
                cleanup_handle.value,
                user_sid,
                windows_module._FILE_WRITE_ATTRIBUTES,
            )
            windows_module._restore_dacl_auto_inherited_handle(
                cleanup_handle.value,
                original_auto_inherited,
            )
        finally:
            cleanup_handle.close()
            windows_module._local_free(user_sid)

    assert _dacl_sddl(existing) == original_sddl
    os.link(existing, alias)
    assert alias.samefile(existing)
    alias.unlink()
    _assert_profile_removed(first_folder)
    _assert_profile_removed(second_folder)


def test_second_lease_rejects_missing_shared_guard_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recovery_root = tmp_path / "worker-acl-recovery"
    monkeypatch.setattr(
        windows_module,
        "_acl_recovery_directory_path",
        lambda: recovery_root,
    )
    approved = tmp_path / "missing-shared-guard-runtime"
    approved.mkdir()
    existing = approved / "existing-resource.bin"
    existing.write_bytes(b"synthetic-runtime-resource")
    before = (_dacl_sddl(approved), _dacl_sddl(existing))
    first = _AppContainerProfile.create((approved,))
    second = _AppContainerProfile.create((approved,))
    first_folder = first.local_app_data
    second_folder = second.local_app_data
    journal = AclRecoveryJournal(recovery_root)
    try:
        first.grant_read_execute((approved,))
        first_entry = journal.inventory()[0]
        inventory = first.access.inventory
        assert inventory is not None
        resource = inventory.resources_by_path[windows_module._normalized_path(existing)]
        baseline = resource.hardlink_guard_baseline
        expected_auto_inherited = resource.original_auto_inherited
        assert baseline is not None
        assert expected_auto_inherited is not None
        peer_sid = windows_module._convert_string_sid(
            windows_module._current_process_user_sid_string()
        )
        try:
            windows_module._remove_one_exact_deny_ace_handle(
                resource.handle.value,
                peer_sid,
                windows_module._FILE_WRITE_ATTRIBUTES,
            )
            windows_module._restore_dacl_auto_inherited_handle(
                resource.handle.value,
                expected_auto_inherited,
            )
            assert (
                windows_module._exact_deny_ace_count_handle(
                    resource.handle.value,
                    peer_sid,
                    windows_module._FILE_WRITE_ATTRIBUTES,
                )
                == baseline
            )
        finally:
            windows_module._local_free(peer_sid)

        with pytest.raises(WindowsLaunchError) as raised:
            second.grant_read_execute((approved,))

        assert raised.value.context == "access.guard_verify"
        assert second.access.inventory is None
        assert journal.inventory() == (first_entry,)
        assert windows_module._sid_access_mask(approved, second.sid) == 0
        assert windows_module._sid_access_mask(existing, second.sid) == 0
        assert second.close_no_raise()
        assert first.close_no_raise()
        assert (_dacl_sddl(approved), _dacl_sddl(existing)) == before
    finally:
        assert second.close_no_raise()
        assert first.close_no_raise()
    _assert_profile_removed(first_folder)
    _assert_profile_removed(second_folder)


def test_last_shared_guard_removal_failure_is_idempotent_across_leases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved = tmp_path / "overlapping-cleanup-runtime"
    approved.mkdir()
    existing = approved / "existing-resource.bin"
    existing.write_bytes(b"synthetic-runtime-resource")
    alias_parent = tmp_path / "late-alias-parent"
    alias = alias_parent / "existing-resource-alias.bin"
    before = (_dacl_sddl(approved), _dacl_sddl(existing))
    first = _AppContainerProfile.create()
    second = _AppContainerProfile.create()
    first_folder = first.local_app_data
    second_folder = second.local_app_data
    original_remove = windows_module._remove_one_exact_deny_ace_handle
    interrupted = False

    first.grant_read_execute((approved,))
    second.grant_read_execute((approved,))
    alias_parent.mkdir()
    assert first.close_no_raise()
    second_inventory = second.access.inventory
    assert second_inventory is not None
    second_resource = second_inventory.resources_by_path[windows_module._normalized_path(existing)]
    peer_sid_string = second.access.peer_sid_string
    assert peer_sid_string is not None
    peer_sid = windows_module._convert_string_sid(peer_sid_string)
    try:
        assert (
            windows_module._exact_deny_ace_count_handle(
                second_resource.handle.value,
                peer_sid,
                windows_module._FILE_WRITE_ATTRIBUTES,
            )
            == 1
        )
    finally:
        windows_module._local_free(peer_sid)
    with pytest.raises(PermissionError):
        os.link(existing, alias)

    def remove_then_fail(handle: int, sid: ctypes.c_void_p, mask: int) -> None:
        nonlocal interrupted
        original_remove(handle, sid, mask)
        if not interrupted:
            interrupted = True
            raise WindowsLaunchError(
                WindowsLaunchErrorCode.ACCESS_GRANT_FAILED,
                "access.guard_remove",
                "Synthetic sanitized post-apply failure.",
            )

    monkeypatch.setattr(
        windows_module,
        "_remove_one_exact_deny_ace_handle",
        remove_then_fail,
    )
    assert not second.close_no_raise()
    assert second.access.inventory is second_inventory
    assert interrupted
    peer_sid = windows_module._convert_string_sid(peer_sid_string)
    try:
        assert (
            windows_module._exact_deny_ace_count_handle(
                second_resource.handle.value,
                peer_sid,
                windows_module._FILE_WRITE_ATTRIBUTES,
            )
            == 0
        )
    finally:
        windows_module._local_free(peer_sid)

    monkeypatch.setattr(
        windows_module,
        "_remove_one_exact_deny_ace_handle",
        original_remove,
    )
    assert second.close_no_raise()

    assert (_dacl_sddl(approved), _dacl_sddl(existing)) == before
    os.link(existing, alias)
    assert alias.samefile(existing)
    alias.unlink()
    alias_parent.rmdir()
    _assert_profile_removed(first_folder)
    _assert_profile_removed(second_folder)


def test_pre_apply_guard_count_failure_retries_without_losing_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved = tmp_path / "count-retry-runtime"
    approved.mkdir()
    existing = approved / "existing-resource.bin"
    existing.write_bytes(b"synthetic-runtime-resource")
    alias_parent = tmp_path / "late-alias-parent"
    alias = alias_parent / "existing-resource-alias.bin"
    before = (_dacl_sddl(approved), _dacl_sddl(existing))
    profile = _AppContainerProfile.create()
    profile_folder = profile.local_app_data
    profile.grant_read_execute((approved,))
    alias_parent.mkdir()
    original_count = windows_module._exact_deny_ace_count_handle
    failed = False

    def fail_first_count(handle: int, sid: ctypes.c_void_p, mask: int) -> int:
        nonlocal failed
        if not failed:
            failed = True
            raise WindowsLaunchError(
                WindowsLaunchErrorCode.ACCESS_GRANT_FAILED,
                "access.guard_read",
                "Synthetic sanitized pre-apply failure.",
            )
        return original_count(handle, sid, mask)

    monkeypatch.setattr(
        windows_module,
        "_exact_deny_ace_count_handle",
        fail_first_count,
    )
    assert not profile.close_no_raise()
    inventory = profile.access.inventory
    assert inventory is not None
    resource = inventory.resources_by_path[windows_module._normalized_path(existing)]
    assert resource.hardlink_guard_added
    assert not resource.hardlink_guard_uncertain
    with pytest.raises(PermissionError):
        os.link(existing, alias)

    monkeypatch.setattr(
        windows_module,
        "_exact_deny_ace_count_handle",
        original_count,
    )
    assert profile.close_no_raise()
    assert (_dacl_sddl(approved), _dacl_sddl(existing)) == before
    os.link(existing, alias)
    alias.unlink()
    alias_parent.rmdir()
    _assert_profile_removed(profile_folder)


def test_null_dacl_resource_is_rejected_without_changing_its_semantics(tmp_path: Path) -> None:
    approved = tmp_path / "null-dacl-runtime"
    approved.mkdir()
    original_sddl = _dacl_sddl(approved)
    profile = _AppContainerProfile.create()
    profile_folder = profile.local_app_data
    try:
        _set_null_dacl(approved)
        assert _dacl_is_null(approved)

        with pytest.raises(WindowsLaunchError) as raised:
            profile.grant_read_execute((approved,))

        assert raised.value.code is WindowsLaunchErrorCode.ACCESS_GRANT_FAILED
        assert raised.value.context == "access.inspect"
        assert _dacl_is_null(approved)
    finally:
        assert profile.close_no_raise()
        _restore_dacl_sddl(approved, original_sddl)

    assert _dacl_sddl(approved) == original_sddl
    _assert_profile_removed(profile_folder)


def test_mid_revoke_failure_retains_authenticated_cleanup_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved = tmp_path / "revoke-failure-runtime"
    approved.mkdir()
    existing = approved / "existing-resource.bin"
    alias = tmp_path / "existing-resource-alias.bin"
    existing.write_bytes(b"synthetic-runtime-resource")
    before = (_dacl_sddl(approved), _dacl_sddl(existing))
    profile = _AppContainerProfile.create()
    profile_folder = profile.local_app_data
    profile.grant_read_execute((approved,))
    original_change = windows_module._change_sid_access_handle
    revoke_calls = 0

    def fail_second_revoke(handle: int, sid: ctypes.c_void_p, mode: int) -> None:
        nonlocal revoke_calls
        if mode == windows_module._REVOKE_ACCESS:
            revoke_calls += 1
        if mode == windows_module._REVOKE_ACCESS and revoke_calls == 2:
            raise WindowsLaunchError(
                WindowsLaunchErrorCode.ACCESS_GRANT_FAILED,
                "access.apply",
                "Synthetic sanitized failure.",
            )
        original_change(handle, sid, mode)

    monkeypatch.setattr(
        windows_module,
        "_change_sid_access_handle",
        fail_second_revoke,
    )
    assert not profile.close_no_raise()
    inventory = profile.access.inventory
    assert inventory is not None
    assert any(
        windows_module._sid_access_mask_handle(resource.handle.value, profile.sid)
        for resource in inventory.resources
    )
    resource = inventory.resources_by_path[windows_module._normalized_path(existing)]
    baseline = resource.hardlink_guard_baseline
    assert baseline is not None
    peer_sid = windows_module._convert_string_sid(windows_module._current_process_user_sid_string())
    try:
        assert (
            windows_module._exact_deny_ace_count_handle(
                resource.handle.value,
                peer_sid,
                windows_module._FILE_WRITE_ATTRIBUTES,
            )
            == baseline + 1
        )
    finally:
        windows_module._local_free(peer_sid)
    with pytest.raises(PermissionError):
        os.link(existing, alias)

    monkeypatch.setattr(windows_module, "_change_sid_access_handle", original_change)
    assert profile.close_no_raise()
    assert (_dacl_sddl(approved), _dacl_sddl(existing)) == before
    os.link(existing, alias)
    alias.unlink()
    _assert_profile_removed(profile_folder)


def test_sid_absence_query_failure_retains_guard_until_successful_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved = tmp_path / "verify-failure-runtime"
    approved.mkdir()
    existing = approved / "existing-resource.bin"
    alias = tmp_path / "existing-resource-alias.bin"
    existing.write_bytes(b"synthetic-runtime-resource")
    before = (_dacl_sddl(approved), _dacl_sddl(existing))
    profile = _AppContainerProfile.create()
    profile_folder = profile.local_app_data
    profile.grant_read_execute((approved,))
    original_mask = windows_module._sid_access_mask_handle
    failed = False

    def fail_first_absence_query(handle: int, sid: ctypes.c_void_p) -> int:
        nonlocal failed
        if not failed:
            failed = True
            raise WindowsLaunchError(
                WindowsLaunchErrorCode.ACCESS_GRANT_FAILED,
                "access.inspect",
                "Synthetic sanitized failure.",
            )
        return original_mask(handle, sid)

    monkeypatch.setattr(
        windows_module,
        "_sid_access_mask_handle",
        fail_first_absence_query,
    )
    assert not profile.close_no_raise()
    inventory = profile.access.inventory
    assert inventory is not None
    assert all(
        original_mask(resource.handle.value, profile.sid) == 0 for resource in inventory.resources
    )
    resource = inventory.resources_by_path[windows_module._normalized_path(existing)]
    baseline = resource.hardlink_guard_baseline
    assert baseline is not None
    peer_sid = windows_module._convert_string_sid(windows_module._current_process_user_sid_string())
    try:
        assert (
            windows_module._exact_deny_ace_count_handle(
                resource.handle.value,
                peer_sid,
                windows_module._FILE_WRITE_ATTRIBUTES,
            )
            == baseline + 1
        )
    finally:
        windows_module._local_free(peer_sid)
    with pytest.raises(PermissionError):
        os.link(existing, alias)

    monkeypatch.setattr(windows_module, "_sid_access_mask_handle", original_mask)
    assert profile.close_no_raise()
    assert (_dacl_sddl(approved), _dacl_sddl(existing)) == before
    os.link(existing, alias)
    alias.unlink()
    _assert_profile_removed(profile_folder)


def test_missing_active_journal_is_republished_for_exact_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recovery_root = tmp_path / "worker-acl-recovery"
    monkeypatch.setattr(
        windows_module,
        "_acl_recovery_directory_path",
        lambda: recovery_root,
    )
    approved = tmp_path / "missing-ledger-runtime"
    approved.mkdir()
    existing = approved / "existing-resource.bin"
    alias = tmp_path / "existing-resource-alias.bin"
    existing.write_bytes(b"synthetic-runtime-resource")
    before = (_dacl_sddl(approved), _dacl_sddl(existing))
    profile = _AppContainerProfile.create()
    profile_folder = profile.local_app_data
    profile.grant_read_execute((approved,))
    journal = AclRecoveryJournal(recovery_root)
    entries = journal.inventory()
    assert len(entries) == 1
    assert entries[0].phase is AclRecoveryPhase.ACTIVE
    journal.delete(profile.access.profile_id)

    assert profile.close_no_raise()

    assert journal.inventory() == ()
    assert (_dacl_sddl(approved), _dacl_sddl(existing)) == before
    os.link(existing, alias)
    alias.unlink()
    _assert_profile_removed(profile_folder)


def test_revoking_republish_failure_retains_handles_sid_and_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recovery_root = tmp_path / "worker-acl-recovery"
    monkeypatch.setattr(
        windows_module,
        "_acl_recovery_directory_path",
        lambda: recovery_root,
    )
    approved = tmp_path / "republish-failure-runtime"
    approved.mkdir()
    existing = approved / "existing-resource.bin"
    alias = tmp_path / "existing-resource-alias.bin"
    existing.write_bytes(b"synthetic-runtime-resource")
    before = (_dacl_sddl(approved), _dacl_sddl(existing))
    profile = _AppContainerProfile.create()
    profile_folder = profile.local_app_data
    profile.grant_read_execute((approved,))
    journal = AclRecoveryJournal(recovery_root)
    original_write = AclRecoveryJournal.write
    journal.delete(profile.access.profile_id)

    def fail_write(
        _journal: AclRecoveryJournal,
        _entry: AclRecoveryEntry,
    ) -> None:
        raise AclRecoveryJournalError("write.replace")

    monkeypatch.setattr(AclRecoveryJournal, "write", fail_write)
    assert not profile.close_no_raise()
    inventory = profile.access.inventory
    assert inventory is not None
    assert any(
        windows_module._sid_access_mask_handle(resource.handle.value, profile.sid)
        for resource in inventory.resources
    )
    resource = inventory.resources_by_path[windows_module._normalized_path(existing)]
    baseline = resource.hardlink_guard_baseline
    assert baseline is not None
    peer_sid = windows_module._convert_string_sid(windows_module._current_process_user_sid_string())
    try:
        assert (
            windows_module._exact_deny_ace_count_handle(
                resource.handle.value,
                peer_sid,
                windows_module._FILE_WRITE_ATTRIBUTES,
            )
            == baseline + 1
        )
    finally:
        windows_module._local_free(peer_sid)
    with pytest.raises(PermissionError):
        os.link(existing, alias)
    assert journal.inventory() == ()

    monkeypatch.setattr(AclRecoveryJournal, "write", original_write)
    assert profile.close_no_raise()
    assert journal.inventory() == ()
    assert (_dacl_sddl(approved), _dacl_sddl(existing)) == before
    os.link(existing, alias)
    alias.unlink()
    _assert_profile_removed(profile_folder)


def test_access_grant_journal_precedes_acl_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved = tmp_path / "approved-runtime"
    approved.mkdir()
    (approved / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    before = _dacl_sddl(approved)
    profile = _AppContainerProfile.create()
    profile_folder = profile.local_app_data
    original_change = windows_module._change_sid_access_handle
    interrupted = False

    def apply_then_interrupt(handle: int, sid: ctypes.c_void_p, mode: int) -> None:
        nonlocal interrupted
        original_change(handle, sid, mode)
        if mode == windows_module._GRANT_ACCESS and not interrupted:
            interrupted = True
            raise KeyboardInterrupt

    monkeypatch.setattr(
        windows_module,
        "_change_sid_access_handle",
        apply_then_interrupt,
    )
    with pytest.raises(KeyboardInterrupt):
        profile.grant_read_execute((approved,))

    inventory = profile.access.inventory
    assert inventory is not None
    approved_resource = inventory.resources_by_path[windows_module._normalized_path(approved)]
    assert approved_resource.grant_started
    assert windows_module._sid_access_mask_handle(
        approved_resource.handle.value,
        profile.sid,
    )
    monkeypatch.setattr(windows_module, "_change_sid_access_handle", original_change)
    assert profile.close_no_raise()
    assert _dacl_sddl(approved) == before
    _assert_profile_removed(profile_folder)


def test_pre_guard_hardlink_race_rejects_grant_and_cleans_exact_object(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved = tmp_path / "pre-guard-race-runtime"
    approved.mkdir()
    existing = approved / "module.py"
    alias_parent = tmp_path / "late-alias-parent"
    alias = alias_parent / "module-alias.py"
    existing.write_text("VALUE = 1\n", encoding="utf-8")
    before = _dacl_sddl(existing)
    profile = _AppContainerProfile.create()
    profile_folder = profile.local_app_data
    original_append = windows_module._append_exact_deny_ace_handle
    injected = False

    def link_then_guard(handle: int, sid: ctypes.c_void_p, mask: int) -> None:
        nonlocal injected
        if not injected:
            injected = True
            alias_parent.mkdir()
            os.link(existing, alias)
        original_append(handle, sid, mask)

    monkeypatch.setattr(
        windows_module,
        "_append_exact_deny_ace_handle",
        link_then_guard,
    )
    with pytest.raises(WindowsLaunchError) as raised:
        profile.grant_read_execute((approved,))

    assert injected
    assert raised.value.context == "access.identity"
    inventory = profile.access.inventory
    assert inventory is not None
    resource = inventory.resources_by_path[windows_module._normalized_path(existing)]
    assert windows_module._query_retained_resource_info(resource.handle.value).link_count == 2
    assert windows_module._sid_access_mask_handle(resource.handle.value, profile.sid) == 0
    assert profile.close_no_raise()
    assert _dacl_sddl(existing) == before
    alias.unlink()
    alias_parent.rmdir()
    _assert_profile_removed(profile_folder)


def test_partial_grant_failure_removes_hardlink_guard_and_restores_dacl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved = tmp_path / "partial-grant-runtime"
    approved.mkdir()
    existing = approved / "module.py"
    alias = tmp_path / "module-alias.py"
    existing.write_text("VALUE = 1\n", encoding="utf-8")
    before = _dacl_sddl(existing)
    profile = _AppContainerProfile.create()
    profile_folder = profile.local_app_data
    original_change = windows_module._change_sid_access_handle
    failed = False

    def fail_first_grant(handle: int, sid: ctypes.c_void_p, mode: int) -> None:
        nonlocal failed
        if mode == windows_module._GRANT_ACCESS and not failed:
            failed = True
            raise WindowsLaunchError(
                WindowsLaunchErrorCode.ACCESS_GRANT_FAILED,
                "access.apply",
                "Synthetic sanitized failure.",
            )
        original_change(handle, sid, mode)

    monkeypatch.setattr(
        windows_module,
        "_change_sid_access_handle",
        fail_first_grant,
    )
    with pytest.raises(WindowsLaunchError):
        profile.grant_read_execute((approved,))

    inventory = profile.access.inventory
    assert inventory is not None
    resource = inventory.resources_by_path[windows_module._normalized_path(existing)]
    assert resource.hardlink_guard_added
    assert windows_module._sid_access_mask_handle(resource.handle.value, profile.sid) == 0
    with pytest.raises(PermissionError):
        os.link(existing, alias)

    assert profile.close_no_raise()
    assert _dacl_sddl(existing) == before
    os.link(existing, alias)
    alias.unlink()
    _assert_profile_removed(profile_folder)


def test_alias_removal_is_blocked_until_handle_based_cleanup_finishes(
    tmp_path: Path,
) -> None:
    approved = tmp_path / "alias-removal-runtime"
    approved.mkdir()
    existing = approved / "existing-resource.bin"
    existing.write_bytes(b"synthetic-runtime-resource")
    before = (_dacl_sddl(approved), _dacl_sddl(existing))
    displaced = tmp_path / "displaced-runtime"
    profile = _AppContainerProfile.create()
    profile_folder = profile.local_app_data

    profile.grant_read_execute((approved,))
    with pytest.raises(OSError):
        existing.unlink()
    with pytest.raises(OSError):
        approved.rename(displaced)

    assert profile.close_no_raise()
    assert (_dacl_sddl(approved), _dacl_sddl(existing)) == before
    approved.rename(displaced)
    displaced.rename(approved)
    existing.unlink()
    existing.write_bytes(b"synthetic-runtime-resource")
    _assert_profile_removed(profile_folder)


@pytest.mark.parametrize("partial", (False, True))
def test_profile_cleanup_retries_sid_release_without_redeleting_profile(
    partial: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delete_profile = Mock(return_value=0)
    free_sid = Mock(side_effect=(1, 0))
    monkeypatch.setattr(
        windows_module,
        "_userenv",
        lambda: SimpleNamespace(DeleteAppContainerProfile=delete_profile),
    )
    monkeypatch.setattr(
        windows_module,
        "_advapi32",
        lambda: SimpleNamespace(FreeSid=free_sid),
    )
    if partial:
        profile = cast(
            Any,
            windows_module._PartialAppContainerProfile(
                name="synthetic-profile",
                sid=ctypes.c_void_p(101),
            ),
        )
    else:
        profile = cast(
            Any,
            _AppContainerProfile(
                name="synthetic-profile",
                sid=ctypes.c_void_p(103),
                local_app_data=Path(r"C:\synthetic-profile"),
                access=cast(Any, SimpleNamespace(close_no_raise=Mock(return_value=True))),
            ),
        )

    assert not profile.close_no_raise()
    assert profile._deleted
    assert profile.close_no_raise()
    assert profile._closed
    delete_profile.assert_called_once_with("synthetic-profile")
    assert free_sid.call_count == 2


def test_close_cleans_job_and_profile_even_when_transport_close_raises(
    monkeypatch: pytest.MonkeyPatch,
    worker_test_resources: WorkerTestResources,
) -> None:
    process = WindowsProcessLauncher(_launch_config(worker_test_resources)).launch(
        _launch_request()
    )
    profile_folder = process._profile.local_app_data
    original_transport_close = process.transport.close

    def fail_transport_close(_transport: object) -> None:
        original_transport_close()
        raise RuntimeError("synthetic transport close failure")

    monkeypatch.setattr(type(process.transport), "close", fail_transport_close)
    with pytest.raises(WindowsLaunchError) as raised:
        process.close()
    original_transport_close()

    assert raised.value.code is WindowsLaunchErrorCode.CLEANUP_FAILED
    assert raised.value.context == "process.close"
    assert process.poll() is not None
    _assert_profile_removed(profile_folder)


def test_close_retries_profile_cleanup_after_a_transient_revoke_failure(
    monkeypatch: pytest.MonkeyPatch,
    worker_test_resources: WorkerTestResources,
) -> None:
    process = WindowsProcessLauncher(_launch_config(worker_test_resources)).launch(
        _launch_request()
    )
    profile_folder = process._profile.local_app_data
    profile_type = type(process._profile)
    original_close = profile_type.close_no_raise
    attempts = 0

    def fail_once(profile: _AppContainerProfile) -> bool:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return False
        return original_close(profile)

    monkeypatch.setattr(profile_type, "close_no_raise", fail_once)
    process.close()
    assert attempts == 2
    _assert_profile_removed(profile_folder)
    process.close()


@pytest.mark.parametrize("endpoint_type", (_WindowsPipeReader, _WindowsPipeWriter))
def test_pipe_endpoint_close_retries_retained_handle_after_marking_closed(
    endpoint_type: type[_WindowsPipeReader] | type[_WindowsPipeWriter],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def fail_once(_handle: int) -> bool:
        nonlocal attempts
        attempts += 1
        return attempts > 1

    monkeypatch.setattr(windows_module, "_close_raw_handle", fail_once)
    owned = _OwnedHandle(123)
    endpoint = endpoint_type(owned)

    with pytest.raises(WindowsLaunchError) as raised:
        endpoint.close()

    assert endpoint.closed
    assert raised.value.code is WindowsLaunchErrorCode.PIPE_FAILED
    assert raised.value.context == "pipe.close"
    assert owned.value == 123

    endpoint.close()
    assert attempts == 2
    with pytest.raises(WindowsLaunchError):
        _ = owned.value


@pytest.mark.parametrize(
    ("read_value", "write_value", "expected_closed"),
    ((911, 0, 911), (0, 919, 919)),
)
def test_create_pipe_adopts_and_closes_every_valid_partial_handle(
    read_value: int,
    write_value: int,
    expected_closed: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[int] = []

    def create_partial_pipe(
        read_pointer: Any,
        write_pointer: Any,
        _attributes: Any,
        _size: int,
    ) -> bool:
        ctypes.cast(read_pointer, ctypes.POINTER(wintypes.HANDLE)).contents.value = read_value
        ctypes.cast(write_pointer, ctypes.POINTER(wintypes.HANDLE)).contents.value = write_value
        return True

    def close_partial_handle(handle: int) -> bool:
        closed.append(handle)
        return True

    monkeypatch.setattr(
        windows_module,
        "_kernel32",
        lambda: SimpleNamespace(CreatePipe=create_partial_pipe),
    )
    monkeypatch.setattr(
        windows_module,
        "_close_raw_handle",
        close_partial_handle,
    )

    with pytest.raises(WindowsLaunchError) as raised:
        windows_module._create_pipe(child_reads=True)

    assert raised.value.code is WindowsLaunchErrorCode.PIPE_FAILED
    assert raised.value.context == "pipe.handles"
    assert closed == [expected_closed]


def test_create_pipe_allocates_cleanup_owners_before_calling_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_pipe = Mock()

    def fail_owner_allocation(_cls: type[_OwnedHandle]) -> _OwnedHandle:
        raise MemoryError

    monkeypatch.setattr(
        windows_module,
        "_kernel32",
        lambda: SimpleNamespace(CreatePipe=create_pipe),
    )
    monkeypatch.setattr(
        _OwnedHandle,
        "empty",
        classmethod(fail_owner_allocation),
    )

    with pytest.raises(MemoryError):
        windows_module._create_pipe(child_reads=True)

    create_pipe.assert_not_called()


def test_create_pipe_sweeps_both_raw_handles_when_the_call_is_interrupted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[int] = []

    def interrupt_after_creating_pipe(
        read_pointer: Any,
        write_pointer: Any,
        _attributes: Any,
        _size: int,
    ) -> bool:
        ctypes.cast(read_pointer, ctypes.POINTER(wintypes.HANDLE)).contents.value = 929
        ctypes.cast(write_pointer, ctypes.POINTER(wintypes.HANDLE)).contents.value = 937
        raise KeyboardInterrupt

    def record_close(handle: int) -> bool:
        closed.append(handle)
        return True

    monkeypatch.setattr(
        windows_module,
        "_kernel32",
        lambda: SimpleNamespace(CreatePipe=interrupt_after_creating_pipe),
    )
    monkeypatch.setattr(windows_module, "_close_raw_handle", record_close)

    with pytest.raises(KeyboardInterrupt):
        windows_module._create_pipe(child_reads=True)

    assert closed == [929, 937]


def test_create_pipe_sweeps_unattached_raw_after_second_adoption_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[int] = []
    adoption_calls = 0
    original_adopt = _OwnedHandle._adopt_valid

    def create_pipe(
        read_pointer: Any,
        write_pointer: Any,
        _attributes: Any,
        _size: int,
    ) -> bool:
        ctypes.cast(read_pointer, ctypes.POINTER(wintypes.HANDLE)).contents.value = 941
        ctypes.cast(write_pointer, ctypes.POINTER(wintypes.HANDLE)).contents.value = 943
        return True

    def interrupt_second_adoption(owner: _OwnedHandle, value: int) -> None:
        nonlocal adoption_calls
        adoption_calls += 1
        if adoption_calls == 2:
            raise KeyboardInterrupt
        original_adopt(owner, value)

    def record_close(handle: int) -> bool:
        closed.append(handle)
        return True

    monkeypatch.setattr(
        windows_module,
        "_kernel32",
        lambda: SimpleNamespace(CreatePipe=create_pipe),
    )
    monkeypatch.setattr(_OwnedHandle, "_adopt_valid", interrupt_second_adoption)
    monkeypatch.setattr(windows_module, "_close_raw_handle", record_close)

    with pytest.raises(KeyboardInterrupt):
        windows_module._create_pipe(child_reads=True)

    assert adoption_calls == 3
    assert closed == [941, 943]


def test_partial_pipe_set_failure_deinherits_and_retains_unclosed_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_create_pipe = windows_module._create_pipe
    original_close = windows_module._close_raw_handle
    created: list[tuple[_OwnedHandle, _OwnedHandle]] = []

    def fail_after_first_pair(*, child_reads: bool) -> tuple[_OwnedHandle, _OwnedHandle]:
        if created:
            raise WindowsLaunchError(
                WindowsLaunchErrorCode.PIPE_FAILED,
                "pipe.synthetic",
                "Synthetic sanitized failure.",
            )
        pair = original_create_pipe(child_reads=child_reads)
        created.append(pair)
        return pair

    monkeypatch.setattr(windows_module, "_create_pipe", fail_after_first_pair)
    child_handle = 0
    close_attempts = 0

    def fail_child_close(handle: int) -> bool:
        nonlocal close_attempts
        if created and handle == created[0][0].value:
            close_attempts += 1
            return False
        return original_close(handle)

    monkeypatch.setattr(windows_module, "_close_raw_handle", fail_child_close)
    with pytest.raises(WindowsLaunchError) as raised:
        _PipeSet.create()

    assert raised.value.code is WindowsLaunchErrorCode.CLEANUP_FAILED
    assert raised.value.context == "pipe.partial_cleanup"
    assert close_attempts == windows_module._HANDLE_CLOSE_ATTEMPTS
    child_handle = created[0][0].value
    flags = wintypes.DWORD()
    get_information = ctypes.WinDLL("kernel32", use_last_error=True).GetHandleInformation
    get_information.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    get_information.restype = wintypes.BOOL
    assert get_information(wintypes.HANDLE(child_handle), ctypes.byref(flags))
    assert not flags.value & windows_module._HANDLE_FLAG_INHERIT

    monkeypatch.setattr(windows_module, "_close_raw_handle", original_close)
    assert windows_module._drain_pipe_handle_quarantine()
    assert not get_information(wintypes.HANDLE(child_handle), ctypes.byref(flags))


def test_complete_pipe_set_cleanup_quarantines_failed_parent_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipes = _PipeSet.create()
    original_close = windows_module._close_raw_handle
    retained = pipes.client_read
    raw_handle = retained.value
    attempts = 0

    def fail_parent_close(handle: int) -> bool:
        nonlocal attempts
        if handle == raw_handle:
            attempts += 1
            return False
        return original_close(handle)

    monkeypatch.setattr(windows_module, "_close_raw_handle", fail_parent_close)
    assert not pipes.close_no_raise()
    assert attempts == windows_module._HANDLE_CLOSE_ATTEMPTS
    assert retained.value == raw_handle

    monkeypatch.setattr(windows_module, "_close_raw_handle", original_close)
    assert windows_module._drain_retained_handle_quarantine()
    with pytest.raises(WindowsLaunchError):
        _ = retained.value


def test_job_setup_failure_quarantines_unclosed_job_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_handle = 731
    fake_kernel32 = SimpleNamespace(
        CreateJobObjectW=Mock(return_value=raw_handle),
        SetInformationJobObject=Mock(return_value=False),
    )
    monkeypatch.setattr(windows_module, "_kernel32", lambda: fake_kernel32)
    monkeypatch.setattr(windows_module, "_close_raw_handle", lambda _handle: False)

    with pytest.raises(WindowsLaunchError) as raised:
        _Job(_launch_request().resource_policy)

    assert raised.value.code is WindowsLaunchErrorCode.CLEANUP_FAILED
    assert raised.value.context == "job.cleanup"
    retained = windows_module._RETAINED_HANDLE_QUARANTINE[-1]
    assert retained.value == raw_handle

    monkeypatch.setattr(windows_module, "_close_raw_handle", lambda _handle: True)
    assert windows_module._drain_retained_handle_quarantine()


@pytest.mark.parametrize("factory", ("job", "mutex"))
def test_handle_factories_allocate_cleanup_owner_before_windows_call(
    factory: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel32 = Mock()
    mutex_policy = Mock()

    def fail_owner_allocation(_cls: type[_OwnedHandle]) -> _OwnedHandle:
        raise MemoryError

    monkeypatch.setattr(windows_module, "_kernel32", kernel32)
    monkeypatch.setattr(windows_module, "_acl_mutex_security_descriptor", mutex_policy)
    monkeypatch.setattr(
        _OwnedHandle,
        "empty",
        classmethod(fail_owner_allocation),
    )

    with pytest.raises(MemoryError):
        if factory == "job":
            _Job(_launch_request().resource_policy)
        else:
            _AclMutationMutex()

    kernel32.assert_not_called()
    mutex_policy.assert_not_called()


@pytest.mark.parametrize(("factory", "raw_handle"), (("job", 769), ("mutex", 773)))
def test_handle_factories_sweep_raw_after_adoption_interrupt(
    factory: str,
    raw_handle: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_adopt = _OwnedHandle._adopt_valid
    adoption_calls = 0
    closed: list[int] = []

    def interrupt_first_adoption(owner: _OwnedHandle, value: int) -> None:
        nonlocal adoption_calls
        adoption_calls += 1
        if adoption_calls == 1:
            raise KeyboardInterrupt
        original_adopt(owner, value)

    def record_close(handle: int) -> bool:
        closed.append(handle)
        return True

    if factory == "job":
        kernel32 = SimpleNamespace(
            CreateJobObjectW=Mock(return_value=raw_handle),
            SetInformationJobObject=Mock(return_value=True),
        )
    else:
        kernel32 = SimpleNamespace(CreateMutexW=Mock(return_value=raw_handle))
        monkeypatch.setattr(
            windows_module,
            "_acl_mutex_security_descriptor",
            lambda: ctypes.c_void_p(7730),
        )
        monkeypatch.setattr(windows_module, "_local_free", lambda _value: None)
    monkeypatch.setattr(windows_module, "_kernel32", lambda: kernel32)
    monkeypatch.setattr(_OwnedHandle, "_adopt_valid", interrupt_first_adoption)
    monkeypatch.setattr(windows_module, "_close_raw_handle", record_close)

    with pytest.raises(KeyboardInterrupt):
        if factory == "job":
            _Job(_launch_request().resource_policy)
        else:
            _AclMutationMutex()

    assert adoption_calls == 2
    assert closed == [raw_handle]


def test_acl_mutex_creation_uses_explicit_non_inheritable_security(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, int | bool | str] = {}

    def create_mutex(
        attributes_pointer: Any,
        initially_owned: bool,
        name: str,
    ) -> int:
        attributes = ctypes.cast(
            attributes_pointer,
            ctypes.POINTER(windows_module._SecurityAttributes),
        ).contents
        captured["descriptor"] = int(attributes.security_descriptor or 0)
        captured["inheritable"] = bool(attributes.inherit_handle)
        captured["initially_owned"] = initially_owned
        captured["name"] = name
        ctypes.set_last_error(windows_module._ERROR_SUCCESS)
        return 809

    fake_kernel32 = SimpleNamespace(CreateMutexW=Mock(side_effect=create_mutex))
    monkeypatch.setattr(windows_module, "_kernel32", lambda: fake_kernel32)
    monkeypatch.setattr(
        windows_module,
        "_acl_mutex_security_descriptor",
        lambda: ctypes.c_void_p(8090),
    )
    monkeypatch.setattr(windows_module, "_local_free", lambda _value: None)
    monkeypatch.setattr(windows_module, "_mutex_handle_is_non_inheritable", lambda _handle: True)
    monkeypatch.setattr(windows_module, "_close_raw_handle", lambda _handle: True)

    mutex = _AclMutationMutex()

    assert captured == {
        "descriptor": 8090,
        "inheritable": False,
        "initially_owned": False,
        "name": windows_module._ACL_MUTATION_MUTEX_NAME,
    }
    assert mutex._handle.close_no_raise()


def test_acl_mutex_accepts_only_existing_object_with_exact_security(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def open_existing(*_args: object) -> int:
        ctypes.set_last_error(windows_module._ERROR_ALREADY_EXISTS)
        return 811

    security_matches = Mock(return_value=True)
    fake_kernel32 = SimpleNamespace(CreateMutexW=Mock(side_effect=open_existing))
    monkeypatch.setattr(windows_module, "_kernel32", lambda: fake_kernel32)
    monkeypatch.setattr(
        windows_module,
        "_acl_mutex_security_descriptor",
        lambda: ctypes.c_void_p(8110),
    )
    monkeypatch.setattr(windows_module, "_local_free", lambda _value: None)
    monkeypatch.setattr(windows_module, "_mutex_handle_is_non_inheritable", lambda _handle: True)
    monkeypatch.setattr(windows_module, "_existing_mutex_has_expected_security", security_matches)
    monkeypatch.setattr(windows_module, "_close_raw_handle", lambda _handle: True)

    mutex = _AclMutationMutex()

    security_matches.assert_called_once()
    assert mutex._handle.close_no_raise()


def test_acl_mutex_rejects_existing_object_with_mismatched_security(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def open_existing(*_args: object) -> int:
        ctypes.set_last_error(windows_module._ERROR_ALREADY_EXISTS)
        return 821

    closed: list[int] = []

    def record_close(handle: int) -> bool:
        closed.append(handle)
        return True

    fake_kernel32 = SimpleNamespace(CreateMutexW=Mock(side_effect=open_existing))
    monkeypatch.setattr(windows_module, "_kernel32", lambda: fake_kernel32)
    monkeypatch.setattr(
        windows_module,
        "_acl_mutex_security_descriptor",
        lambda: ctypes.c_void_p(8210),
    )
    monkeypatch.setattr(windows_module, "_local_free", lambda _value: None)
    monkeypatch.setattr(windows_module, "_mutex_handle_is_non_inheritable", lambda _handle: True)
    monkeypatch.setattr(
        windows_module,
        "_existing_mutex_has_expected_security",
        lambda _handle, _descriptor: False,
    )
    monkeypatch.setattr(
        windows_module,
        "_close_raw_handle",
        record_close,
    )

    with pytest.raises(WindowsLaunchError) as raised:
        _AclMutationMutex()

    assert raised.value.code is WindowsLaunchErrorCode.ACCESS_GRANT_FAILED
    assert raised.value.context == "access.lock_policy"
    assert closed == [821]


def test_acl_mutex_closes_existing_handle_when_expected_policy_build_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def open_existing(*_args: object) -> int:
        ctypes.set_last_error(windows_module._ERROR_ALREADY_EXISTS)
        return 823

    policy_calls = 0

    def fail_second_policy_build() -> ctypes.c_void_p:
        nonlocal policy_calls
        policy_calls += 1
        if policy_calls == 2:
            raise WindowsLaunchError(
                WindowsLaunchErrorCode.ACCESS_GRANT_FAILED,
                "access.lock_policy",
                "Synthetic sanitized failure.",
            )
        return ctypes.c_void_p(8230)

    closed: list[int] = []

    def record_close(handle: int) -> bool:
        closed.append(handle)
        return True

    monkeypatch.setattr(
        windows_module,
        "_kernel32",
        lambda: SimpleNamespace(CreateMutexW=Mock(side_effect=open_existing)),
    )
    monkeypatch.setattr(
        windows_module,
        "_acl_mutex_security_descriptor",
        fail_second_policy_build,
    )
    monkeypatch.setattr(windows_module, "_local_free", lambda _value: None)
    monkeypatch.setattr(windows_module, "_mutex_handle_is_non_inheritable", lambda _handle: True)
    monkeypatch.setattr(windows_module, "_close_raw_handle", record_close)

    with pytest.raises(WindowsLaunchError) as raised:
        _AclMutationMutex()

    assert raised.value.context == "access.lock_policy"
    assert closed == [823]


def test_acl_mutex_quarantines_handle_when_existing_policy_query_and_close_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def open_existing(*_args: object) -> int:
        ctypes.set_last_error(windows_module._ERROR_ALREADY_EXISTS)
        return 827

    def fail_query(_handle: int, _descriptor: ctypes.c_void_p) -> bool:
        raise WindowsLaunchError(
            WindowsLaunchErrorCode.ACCESS_GRANT_FAILED,
            "access.lock_policy",
            "Synthetic sanitized failure.",
        )

    monkeypatch.setattr(
        windows_module,
        "_kernel32",
        lambda: SimpleNamespace(CreateMutexW=Mock(side_effect=open_existing)),
    )
    monkeypatch.setattr(
        windows_module,
        "_acl_mutex_security_descriptor",
        lambda: ctypes.c_void_p(8270),
    )
    monkeypatch.setattr(windows_module, "_local_free", lambda _value: None)
    monkeypatch.setattr(windows_module, "_mutex_handle_is_non_inheritable", lambda _handle: True)
    monkeypatch.setattr(windows_module, "_existing_mutex_has_expected_security", fail_query)
    monkeypatch.setattr(windows_module, "_close_raw_handle", lambda _handle: False)

    with pytest.raises(WindowsLaunchError) as raised:
        _AclMutationMutex()

    assert raised.value.code is WindowsLaunchErrorCode.CLEANUP_FAILED
    assert raised.value.context == "access.lock_cleanup"
    assert windows_module._RETAINED_HANDLE_QUARANTINE[-1].value == 827

    monkeypatch.setattr(windows_module, "_close_raw_handle", lambda _handle: True)
    assert windows_module._drain_retained_handle_quarantine()


def test_acl_mutex_retries_release_and_quarantines_failed_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert windows_module._ACL_MUTATION_MUTEX_NAME.startswith("Global\\")
    release = Mock(side_effect=(False, True))
    fake_kernel32 = SimpleNamespace(
        CreateMutexW=Mock(return_value=733),
        WaitForSingleObject=Mock(return_value=windows_module._WAIT_OBJECT_0),
        ReleaseMutex=release,
    )
    monkeypatch.setattr(windows_module, "_kernel32", lambda: fake_kernel32)
    monkeypatch.setattr(
        windows_module,
        "_acl_mutex_security_descriptor",
        lambda: ctypes.c_void_p(7330),
    )
    monkeypatch.setattr(windows_module, "_local_free", lambda _value: None)
    monkeypatch.setattr(windows_module, "_mutex_handle_is_non_inheritable", lambda _handle: True)
    monkeypatch.setattr(windows_module, "_close_raw_handle", lambda _handle: False)
    mutex = _AclMutationMutex()

    with pytest.raises(WindowsLaunchError) as raised:
        with mutex:
            pass

    assert raised.value.code is WindowsLaunchErrorCode.CLEANUP_FAILED
    assert raised.value.context == "access.lock_cleanup"
    assert release.call_count == 2
    assert not mutex._owned
    assert mutex._handle.value == 733

    monkeypatch.setattr(windows_module, "_close_raw_handle", lambda _handle: True)
    assert windows_module._drain_retained_handle_quarantine()


def test_acl_mutex_retains_ownership_until_release_is_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = Mock(return_value=False)
    fake_kernel32 = SimpleNamespace(
        CreateMutexW=Mock(return_value=739),
        WaitForSingleObject=Mock(return_value=windows_module._WAIT_OBJECT_0),
        ReleaseMutex=release,
    )
    monkeypatch.setattr(windows_module, "_kernel32", lambda: fake_kernel32)
    monkeypatch.setattr(
        windows_module,
        "_acl_mutex_security_descriptor",
        lambda: ctypes.c_void_p(7390),
    )
    monkeypatch.setattr(windows_module, "_local_free", lambda _value: None)
    monkeypatch.setattr(windows_module, "_mutex_handle_is_non_inheritable", lambda _handle: True)
    monkeypatch.setattr(windows_module, "_close_raw_handle", lambda _handle: True)
    mutex = _AclMutationMutex()

    with pytest.raises(WindowsLaunchError) as raised:
        with mutex:
            pass

    assert raised.value.code is WindowsLaunchErrorCode.CLEANUP_FAILED
    assert raised.value.context == "access.lock_release"
    assert release.call_count == windows_module._HANDLE_CLOSE_ATTEMPTS
    assert mutex._owned
    assert mutex in windows_module._ACL_MUTEX_QUARANTINE

    release.return_value = True
    assert windows_module._drain_acl_mutex_quarantine()
    assert not mutex._owned


def test_acl_mutex_abandonment_blocks_launch_without_entering_critical_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_kernel32 = SimpleNamespace(
        CreateMutexW=Mock(return_value=829),
        WaitForSingleObject=Mock(return_value=windows_module._WAIT_ABANDONED),
        ReleaseMutex=Mock(return_value=True),
    )
    monkeypatch.setattr(windows_module, "_kernel32", lambda: fake_kernel32)
    monkeypatch.setattr(
        windows_module,
        "_acl_mutex_security_descriptor",
        lambda: ctypes.c_void_p(8290),
    )
    monkeypatch.setattr(windows_module, "_local_free", lambda _value: None)
    monkeypatch.setattr(windows_module, "_mutex_handle_is_non_inheritable", lambda _handle: True)
    monkeypatch.setattr(windows_module, "_close_raw_handle", lambda _handle: True)
    entered = False
    windows_module._PROCESS_CREATION_QUARANTINED = False
    mutex = _AclMutationMutex()
    try:
        with pytest.raises(WindowsLaunchError) as raised:
            with mutex:
                entered = True

        assert raised.value.code is WindowsLaunchErrorCode.CLEANUP_FAILED
        assert raised.value.context == "access.lock_abandoned"
        assert not entered
        assert windows_module._PROCESS_CREATION_QUARANTINED
        assert mutex._owned
        assert mutex in windows_module._ABANDONED_ACL_MUTEX_QUARANTINE
        fake_kernel32.ReleaseMutex.assert_not_called()
    finally:
        assert mutex.release_no_raise()
        assert mutex._handle.close_no_raise()
        with windows_module._ABANDONED_ACL_MUTEX_QUARANTINE_LOCK:
            windows_module._ABANDONED_ACL_MUTEX_QUARANTINE.remove(mutex)
        windows_module._PROCESS_CREATION_QUARANTINED = False


def test_acl_mutex_serializes_two_live_handles_across_threads() -> None:
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    failures: list[BaseException] = []

    def hold_first() -> None:
        try:
            with _AclMutationMutex():
                first_entered.set()
                if not release_first.wait(5.0):
                    raise RuntimeError("synthetic mutex release timeout")
        except BaseException as error:
            failures.append(error)

    def enter_second() -> None:
        try:
            if not first_entered.wait(5.0):
                raise RuntimeError("synthetic mutex acquisition timeout")
            with _AclMutationMutex():
                second_entered.set()
        except BaseException as error:
            failures.append(error)

    first = threading.Thread(target=hold_first)
    second = threading.Thread(target=enter_second)
    first.start()
    second.start()
    try:
        assert first_entered.wait(5.0)
        assert not second_entered.wait(0.2)
    finally:
        release_first.set()
    first.join(timeout=5.0)
    second.join(timeout=5.0)
    assert not first.is_alive()
    assert not second.is_alive()
    assert not failures
    assert second_entered.is_set()


def test_abandoned_live_mutex_blocks_every_following_waiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(windows_module, "_ACL_MUTATION_WAIT_MILLISECONDS", 250)
    crashed: list[_AclMutationMutex] = []
    abandoned: list[_AclMutationMutex] = []
    abandoned_ready = threading.Event()
    release_abandoned = threading.Event()
    third_entered = threading.Event()
    receiver_failures: list[BaseException] = []
    third_errors: list[WindowsLaunchError] = []
    windows_module._PROCESS_CREATION_QUARANTINED = False

    def crash_while_holding_mutex() -> None:
        mutex = _AclMutationMutex()
        mutex.__enter__()
        crashed.append(mutex)

    def receive_abandoned_mutex() -> None:
        mutex = _AclMutationMutex()
        abandoned.append(mutex)
        try:
            mutex.__enter__()
            receiver_failures.append(RuntimeError("abandoned mutex was accepted"))
        except WindowsLaunchError as error:
            if error.context != "access.lock_abandoned":
                receiver_failures.append(error)
            abandoned_ready.set()
            if not release_abandoned.wait(5.0):
                receiver_failures.append(RuntimeError("synthetic abandoned cleanup timeout"))
        except BaseException as error:
            receiver_failures.append(error)
        finally:
            if mutex._owned and not mutex.release_no_raise():
                receiver_failures.append(RuntimeError("test could not release abandoned mutex"))
            if not mutex._handle.close_no_raise():
                receiver_failures.append(RuntimeError("test could not close abandoned mutex"))

    def wait_behind_abandonment() -> None:
        mutex: _AclMutationMutex | None = None
        try:
            mutex = _AclMutationMutex()
            with mutex:
                third_entered.set()
        except WindowsLaunchError as error:
            third_errors.append(error)
        except BaseException as error:
            receiver_failures.append(error)
        finally:
            if mutex is not None and mutex._handle._value:
                mutex._handle.close_no_raise()

    owner = threading.Thread(target=crash_while_holding_mutex)
    owner.start()
    owner.join(timeout=5.0)
    assert not owner.is_alive()
    assert len(crashed) == 1

    receiver = threading.Thread(target=receive_abandoned_mutex)
    receiver.start()
    try:
        assert abandoned_ready.wait(5.0)
        third = threading.Thread(target=wait_behind_abandonment)
        third.start()
        third.join(timeout=2.0)
        assert not third.is_alive()
        assert not third_entered.is_set()
        assert len(third_errors) == 1
        assert third_errors[0].context == "access.lock"
    finally:
        release_abandoned.set()
    receiver.join(timeout=5.0)
    assert not receiver.is_alive()
    assert not receiver_failures

    assert crashed[0]._handle.close_no_raise()
    with windows_module._ABANDONED_ACL_MUTEX_QUARANTINE_LOCK:
        windows_module._ABANDONED_ACL_MUTEX_QUARANTINE[:] = [
            mutex
            for mutex in windows_module._ABANDONED_ACL_MUTEX_QUARANTINE
            if mutex not in abandoned
        ]
    windows_module._PROCESS_CREATION_QUARANTINED = False


def test_process_close_retries_retained_handles_and_keeps_failure_sticky(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = SimpleNamespace(close=Mock())
    profile = SimpleNamespace(close_no_raise=Mock(return_value=True))
    process_handle = _OwnedHandle(743)
    job_handle = _OwnedHandle(751)
    process = WindowsWorkerProcess(
        pid=17,
        process=process_handle,
        job=cast(Any, SimpleNamespace(handle=job_handle)),
        profile=cast(Any, profile),
        transport=cast(Any, transport),
        isolation=_isolation_evidence(),
    )
    process._tree_confirmed = True
    process._returncode = 0
    monkeypatch.setattr(windows_module, "_close_raw_handle", lambda _handle: False)

    with pytest.raises(WindowsLaunchError) as raised:
        process.close()

    assert raised.value.code is WindowsLaunchErrorCode.CLEANUP_FAILED
    assert process.__getattribute__("_containment_closed") is False
    assert process.__getattribute__("_closed") is False
    assert process_handle.value == 743
    assert job_handle.value == 751
    profile.close_no_raise.assert_called_once_with()

    monkeypatch.setattr(windows_module, "_close_raw_handle", lambda _handle: True)
    with pytest.raises(WindowsLaunchError) as retried:
        process.close()
    assert retried.value.code is WindowsLaunchErrorCode.CLEANUP_FAILED
    assert process.__getattribute__("_containment_closed") is True
    assert process.__getattribute__("_closed") is True
    profile.close_no_raise.assert_called_once_with()
    with pytest.raises(WindowsLaunchError):
        process.close()
    assert windows_module._drain_retained_handle_quarantine()


def test_process_close_stops_live_tree_before_closing_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    transport = SimpleNamespace(close=lambda: order.append("transport"))
    profile = SimpleNamespace(close_no_raise=Mock(return_value=True))
    process = WindowsWorkerProcess(
        pid=19,
        process=_OwnedHandle(1067),
        job=cast(Any, SimpleNamespace(handle=_OwnedHandle(1069))),
        profile=cast(Any, profile),
        transport=cast(Any, transport),
        isolation=_isolation_evidence(),
    )
    process._returncode = 0

    def stop_tree(_job: object, _timeout_seconds: float) -> bool:
        order.append("tree")
        return True

    monkeypatch.setattr(windows_module, "_stop_job_tree_no_raise", stop_tree)
    monkeypatch.setattr(windows_module, "_close_raw_handle", lambda _handle: True)

    process.close()

    assert order == ["tree", "transport"]
    assert process.__getattribute__("_closed")
    profile.close_no_raise.assert_called_once_with()


def test_client_disposal_cleans_confirmed_profile_before_sticky_handle_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = SimpleNamespace(close=Mock())
    profile = SimpleNamespace(close_no_raise=Mock(return_value=True))
    process = WindowsWorkerProcess(
        pid=23,
        process=_OwnedHandle(1061),
        job=cast(Any, SimpleNamespace(handle=_OwnedHandle(1063))),
        profile=cast(Any, profile),
        transport=cast(Any, transport),
        isolation=_isolation_evidence(),
    )
    process._tree_confirmed = True
    process._returncode = 0
    client = WorkerClient(
        cast(Any, SimpleNamespace()),
        _launch_request().resource_policy,
        WorkerTimeoutPolicy(
            handshake_seconds=1.0,
            cancellation_grace_seconds=1.0,
            shutdown_grace_seconds=1.0,
        ),
    )
    client._process = process
    client._transport = cast(Any, transport)
    monkeypatch.setattr(windows_module, "_close_raw_handle", lambda _handle: False)

    assert not client._dispose_process(terminate=False)
    assert client._cleanup_unconfirmed
    profile.close_no_raise.assert_called_once_with()
    assert process.__getattribute__("_profile_closed")
    assert not client._dispose_process(terminate=False)
    profile.close_no_raise.assert_called_once_with()

    monkeypatch.setattr(windows_module, "_close_raw_handle", lambda _handle: True)
    assert windows_module._drain_retained_handle_quarantine()


def test_unconfirmed_tree_close_retains_profile_and_never_reports_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = SimpleNamespace(close=Mock())
    profile = SimpleNamespace(close_no_raise=Mock(return_value=True))
    process_handle = _OwnedHandle(757)
    job_handle = _OwnedHandle(761)
    process = WindowsWorkerProcess(
        pid=19,
        process=process_handle,
        job=cast(Any, SimpleNamespace(handle=job_handle)),
        profile=cast(Any, profile),
        transport=cast(Any, transport),
        isolation=_isolation_evidence(),
    )
    monkeypatch.setattr(windows_module, "_stop_job_tree_no_raise", lambda *_args: False)
    monkeypatch.setattr(windows_module, "_terminate_process_no_raise", lambda *_args: True)
    monkeypatch.setattr(windows_module, "_close_raw_handle", lambda _handle: False)

    with pytest.raises(WindowsLaunchError):
        process.close()
    assert process.__getattribute__("_containment_closed") is False
    assert process.__getattribute__("_closed") is False
    assert process.__getattribute__("_profile_cleanup_allowed") is False
    profile_authority = cast(Any, profile)
    assert any(
        entry.profile is profile_authority and not entry.cleanup_allowed
        for entry in windows_module._PROFILE_QUARANTINE
    )
    profile.close_no_raise.assert_not_called()

    monkeypatch.setattr(windows_module, "_close_raw_handle", lambda _handle: True)
    with pytest.raises(WindowsLaunchError):
        process.close()
    assert process.__getattribute__("_closed") is True
    with pytest.raises(WindowsLaunchError):
        process.close()
    assert windows_module._drain_retained_handle_quarantine()
    with windows_module._PROFILE_QUARANTINE_LOCK:
        windows_module._PROFILE_QUARANTINE[:] = [
            entry
            for entry in windows_module._PROFILE_QUARANTINE
            if entry.profile is not profile_authority
        ]


def test_transport_close_is_retried_without_clearing_prior_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = SimpleNamespace(close=Mock(side_effect=(RuntimeError("synthetic"), None)))
    profile = SimpleNamespace(close_no_raise=Mock(return_value=True))
    process = WindowsWorkerProcess(
        pid=23,
        process=_OwnedHandle(769),
        job=cast(Any, SimpleNamespace(handle=_OwnedHandle(773))),
        profile=cast(Any, profile),
        transport=cast(Any, transport),
        isolation=_isolation_evidence(),
    )
    process._tree_confirmed = True
    process._returncode = 0
    monkeypatch.setattr(windows_module, "_close_raw_handle", lambda _handle: True)

    with pytest.raises(WindowsLaunchError):
        process.close()
    assert process.__getattribute__("_closed") is False
    with pytest.raises(WindowsLaunchError):
        process.close()
    assert process.__getattribute__("_closed") is True
    assert transport.close.call_count == 2


def test_wait_rejects_huge_integer_with_stable_error() -> None:
    process = WindowsWorkerProcess(
        pid=29,
        process=_OwnedHandle(787),
        job=cast(Any, SimpleNamespace(handle=_OwnedHandle(797))),
        profile=cast(Any, SimpleNamespace(close_no_raise=Mock(return_value=True))),
        transport=cast(Any, SimpleNamespace(close=Mock())),
        isolation=_isolation_evidence(),
    )

    with pytest.raises(WindowsLaunchError) as raised:
        process.wait(10**1000)

    assert raised.value.code is WindowsLaunchErrorCode.CONFIGURATION_INVALID
    assert raised.value.context == "wait.timeout"


def test_windows_resource_policy_rejects_unrepresentable_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _launch_request()
    policy_type = type(request.resource_policy)
    maximum_cpu_policy = policy_type(
        process_memory_bytes=1,
        job_memory_bytes=1,
        cpu_time_ms=windows_module._MAX_WINDOWS_CPU_TIME_MS,
        maximum_processes=1,
    )
    windows_module._validate_windows_resource_policy(maximum_cpu_policy)
    excessive_cpu_policy = policy_type(
        process_memory_bytes=1,
        job_memory_bytes=1,
        cpu_time_ms=windows_module._MAX_WINDOWS_CPU_TIME_MS + 1,
        maximum_processes=1,
    )
    with pytest.raises(WindowsLaunchError) as cpu_error:
        windows_module._validate_windows_resource_policy(excessive_cpu_policy)
    assert cpu_error.value.context == "resource_policy.cpu_time"

    monkeypatch.setattr(windows_module, "_MAX_WINDOWS_SIZE_T", 1024)
    excessive_memory_policy = policy_type(
        process_memory_bytes=1025,
        job_memory_bytes=2048,
        cpu_time_ms=1,
        maximum_processes=1,
    )
    with pytest.raises(WindowsLaunchError) as memory_error:
        windows_module._validate_windows_resource_policy(excessive_memory_policy)
    assert memory_error.value.context == "resource_policy.memory"


def test_resource_inventory_error_has_no_path_bearing_exception_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource = tmp_path / "runtime-resource"
    resource.mkdir()
    canary = r"C:\private\applicant-canary.pdf"
    original_iterdir = Path.iterdir

    def fail_inventory(path: Path) -> Iterator[Path]:
        if path == resource:
            raise OSError(canary)
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", fail_inventory)
    with pytest.raises(WindowsLaunchError) as raised:
        windows_module._approved_resource_tree(resource)

    error = raised.value
    rendered = "".join(traceback.format_exception(error))
    assert error.__context__ is None
    assert canary not in str(error)
    assert canary not in repr(error)
    assert canary not in rendered


def test_failed_launch_retries_profile_cleanup_and_restores_access_policy(
    monkeypatch: pytest.MonkeyPatch,
    worker_test_resources: WorkerTestResources,
) -> None:
    config = _launch_config(worker_test_resources)
    before = tuple(_dacl_sddl(path) for path in config.access_roots)
    captured: list[_AppContainerProfile] = []
    original_create = _AppContainerProfile.create
    original_close = _AppContainerProfile.close_no_raise
    close_attempts = 0

    def capture_profile(
        _cls: type[_AppContainerProfile],
        recovery_roots: tuple[Path, ...],
    ) -> _AppContainerProfile:
        assert recovery_roots == config.access_roots
        profile = original_create(recovery_roots)
        captured.append(profile)
        return profile

    def fail_first_close(profile: _AppContainerProfile) -> bool:
        nonlocal close_attempts
        close_attempts += 1
        if close_attempts == 1:
            return False
        return original_close(profile)

    def fail_resume(_handle: int) -> None:
        raise WindowsLaunchError(
            WindowsLaunchErrorCode.PROCESS_FAILED,
            "process.resume",
            "Synthetic sanitized failure.",
        )

    monkeypatch.setattr(_AppContainerProfile, "create", classmethod(capture_profile))
    monkeypatch.setattr(_AppContainerProfile, "close_no_raise", fail_first_close)
    monkeypatch.setattr(windows_module, "_resume_thread", fail_resume)

    with pytest.raises(WindowsLaunchError) as raised:
        WindowsProcessLauncher(config).launch(_launch_request())

    assert raised.value.code is WindowsLaunchErrorCode.PROCESS_FAILED
    assert raised.value.context == "process.resume"
    assert close_attempts == 2
    assert len(captured) == 1
    assert tuple(_dacl_sddl(path) for path in config.access_roots) == before
    _assert_profile_removed(captured[0].local_app_data)


def test_partial_process_authority_terminates_owned_process_and_cleans_profile(
    monkeypatch: pytest.MonkeyPatch,
    worker_test_resources: WorkerTestResources,
) -> None:
    process_handle = 941
    profile = SimpleNamespace(
        sid=ctypes.c_void_p(1),
        local_app_data=Path(r"C:\synthetic-app-container"),
        grant_read_execute=Mock(),
        require_read_execute=Mock(),
        require_private_pycache=Mock(),
        close_no_raise=Mock(return_value=True),
    )
    job = SimpleNamespace(
        handle=_OwnedHandle(947),
        assign=Mock(),
    )
    pipes = SimpleNamespace(
        inherited_handles=(953, 959, 967),
        secure_child_ends_no_raise=Mock(return_value=(True, True)),
        close_no_raise=Mock(return_value=True),
    )

    class FakeAttributeList:
        def __init__(self, _sid: ctypes.c_void_p, _handles: tuple[int, ...]) -> None:
            pass

        def __enter__(self) -> FakeAttributeList:
            return self

        def __exit__(self, *_exc: object) -> None:
            pass

    terminated: list[int] = []

    def return_partial_process(
        _self: WindowsProcessLauncher,
        _pipes: Any,
        _attributes: Any,
        _local_app_data: Path,
        information: Any,
    ) -> None:
        information.process = wintypes.HANDLE(process_handle)
        information.thread = wintypes.HANDLE()
        information.process_id = 971
        information.thread_id = 0

    def terminate_owned_process(process: _OwnedHandle, _timeout: float) -> bool:
        terminated.append(process.value)
        return True

    monkeypatch.setattr(
        _AppContainerProfile,
        "create",
        classmethod(lambda _cls, _recovery_roots: profile),
    )
    monkeypatch.setattr(windows_module, "_Job", lambda _policy: job)
    monkeypatch.setattr(_PipeSet, "create", classmethod(lambda _cls: pipes))
    monkeypatch.setattr(windows_module, "_AttributeList", FakeAttributeList)
    monkeypatch.setattr(
        WindowsProcessLauncher,
        "_create_suspended_process",
        return_partial_process,
    )
    monkeypatch.setattr(
        windows_module,
        "_terminate_process_no_raise",
        terminate_owned_process,
    )
    monkeypatch.setattr(windows_module, "_close_raw_handle", lambda _handle: True)
    monkeypatch.setattr(windows_module, "_PROCESS_CREATION_QUARANTINED", False)

    with pytest.raises(WindowsLaunchError) as raised:
        WindowsProcessLauncher(_launch_config(worker_test_resources)).launch(_launch_request())

    assert raised.value.code is WindowsLaunchErrorCode.PROCESS_FAILED
    assert raised.value.context == "process.handles"
    assert terminated == [process_handle]
    job.assign.assert_not_called()
    pipes.close_no_raise.assert_called_once_with()
    profile.close_no_raise.assert_called_once_with()


def test_process_owners_are_allocated_before_profile_or_process_creation(
    monkeypatch: pytest.MonkeyPatch,
    worker_test_resources: WorkerTestResources,
) -> None:
    create_profile = Mock()
    create_process = Mock()

    def fail_owner_allocation(_cls: type[_OwnedHandle]) -> _OwnedHandle:
        raise MemoryError

    monkeypatch.setattr(windows_module, "_drain_launch_quarantines", lambda: True)
    monkeypatch.setattr(
        _OwnedHandle,
        "empty",
        classmethod(fail_owner_allocation),
    )
    monkeypatch.setattr(_AppContainerProfile, "create", create_profile)
    monkeypatch.setattr(
        WindowsProcessLauncher,
        "_create_suspended_process",
        create_process,
    )

    with pytest.raises(MemoryError):
        WindowsProcessLauncher(_launch_config(worker_test_resources)).launch(_launch_request())

    create_profile.assert_not_called()
    create_process.assert_not_called()


@pytest.mark.parametrize("failure_stage", ("call", "second_adoption"))
def test_interrupted_process_creation_is_adopted_before_waiter_and_cleanup(
    failure_stage: str,
    monkeypatch: pytest.MonkeyPatch,
    worker_test_resources: WorkerTestResources,
) -> None:
    process_handle = 1103
    thread_handle = 1109
    profile = SimpleNamespace(
        sid=ctypes.c_void_p(1),
        local_app_data=Path(r"C:\synthetic-app-container"),
        grant_read_execute=Mock(),
        require_read_execute=Mock(),
        require_private_pycache=Mock(),
        close_no_raise=Mock(return_value=True),
    )
    job = SimpleNamespace(handle=_OwnedHandle(1097), assign=Mock())
    pipes = SimpleNamespace(
        inherited_handles=(1087, 1091, 1093),
        secure_child_ends_no_raise=Mock(return_value=(True, True)),
        close_no_raise=Mock(return_value=True),
    )
    waiter_started = threading.Event()
    waiter_completed = threading.Event()
    waiter_errors: list[BaseException] = []
    waiter_acquired = False
    closed: list[int] = []

    class FakeAttributeList:
        def __init__(self, _sid: ctypes.c_void_p, _handles: tuple[int, ...]) -> None:
            pass

        def __enter__(self) -> FakeAttributeList:
            return self

        def __exit__(self, *_exc: object) -> None:
            pass

    def wait_for_creation_authority() -> None:
        nonlocal waiter_acquired
        waiter_started.set()
        try:
            windows_module._acquire_process_creation_authority()
        except BaseException as error:
            waiter_errors.append(error)
        else:
            waiter_acquired = True
            windows_module._PROCESS_CREATION_LOCK.release()
        finally:
            waiter_completed.set()

    waiter: threading.Thread | None = None

    def interrupt_after_process_creation(
        _self: WindowsProcessLauncher,
        _pipes: Any,
        _attributes: Any,
        _local_app_data: Path,
        information: Any,
    ) -> None:
        nonlocal waiter
        waiter = threading.Thread(target=wait_for_creation_authority)
        waiter.start()
        assert waiter_started.wait(1.0)
        information.process = wintypes.HANDLE(process_handle)
        information.thread = wintypes.HANDLE(thread_handle)
        information.process_id = 1117
        information.thread_id = 1123
        if failure_stage == "call":
            raise KeyboardInterrupt

    def record_close(handle: int) -> bool:
        closed.append(handle)
        return True

    terminate = Mock(return_value=False)
    original_adopt = _OwnedHandle._adopt_valid
    adoption_calls = 0

    def interrupt_second_adoption(owner: _OwnedHandle, value: int) -> None:
        nonlocal adoption_calls
        adoption_calls += 1
        if failure_stage == "second_adoption" and adoption_calls == 2:
            raise KeyboardInterrupt
        original_adopt(owner, value)

    monkeypatch.setattr(
        _AppContainerProfile,
        "create",
        classmethod(lambda _cls, _recovery_roots: profile),
    )
    monkeypatch.setattr(windows_module, "_Job", lambda _policy: job)
    monkeypatch.setattr(_PipeSet, "create", classmethod(lambda _cls: pipes))
    monkeypatch.setattr(windows_module, "_AttributeList", FakeAttributeList)
    monkeypatch.setattr(
        WindowsProcessLauncher,
        "_create_suspended_process",
        interrupt_after_process_creation,
    )
    monkeypatch.setattr(windows_module, "_terminate_process_no_raise", terminate)
    monkeypatch.setattr(windows_module, "_close_raw_handle", record_close)
    monkeypatch.setattr(_OwnedHandle, "_adopt_valid", interrupt_second_adoption)
    monkeypatch.setattr(windows_module, "_PROCESS_CREATION_QUARANTINED", False)
    monkeypatch.setattr(windows_module, "_PROCESS_CREATION_CLEANUP_PENDING", False)
    monkeypatch.setattr(windows_module, "_FAILED_LAUNCH_QUARANTINE", [])

    with pytest.raises(WindowsLaunchError) as raised:
        WindowsProcessLauncher(_launch_config(worker_test_resources)).launch(_launch_request())

    assert waiter_completed.wait(2.0)
    assert waiter is not None
    waiter.join(timeout=1.0)
    assert not waiter.is_alive()
    assert raised.value.code is WindowsLaunchErrorCode.CLEANUP_FAILED
    assert raised.value.context == "launch.cleanup"
    assert not waiter_acquired
    assert len(waiter_errors) == 1
    assert isinstance(waiter_errors[0], WindowsLaunchError)
    assert waiter_errors[0].context in {
        "process_creation.quarantine",
        "launch.quarantine",
    }
    assert thread_handle in closed
    assert terminate.call_count >= 2
    assert all(call.args[0].value == process_handle for call in terminate.call_args_list)
    assert len(windows_module._FAILED_LAUNCH_QUARANTINE) == 1
    retained = windows_module._FAILED_LAUNCH_QUARANTINE[0]
    assert retained.process is not None
    assert retained.process.value == process_handle
    assert not retained.process_authority_missing
    assert not windows_module._PROCESS_CREATION_CLEANUP_PENDING
    job.assign.assert_not_called()
    profile.close_no_raise.assert_not_called()


def test_missing_process_authority_quarantines_before_creation_lock_release(
    monkeypatch: pytest.MonkeyPatch,
    worker_test_resources: WorkerTestResources,
) -> None:
    profile = SimpleNamespace(
        sid=ctypes.c_void_p(1),
        local_app_data=Path(r"C:\synthetic-app-container"),
        grant_read_execute=Mock(),
        require_read_execute=Mock(),
        require_private_pycache=Mock(),
        close_no_raise=Mock(return_value=True),
    )
    job = SimpleNamespace(
        handle=_OwnedHandle(977),
        assign=Mock(),
    )
    pipes = SimpleNamespace(
        inherited_handles=(983, 991, 997),
        secure_child_ends_no_raise=Mock(return_value=(True, True)),
        close_no_raise=Mock(return_value=True),
    )
    waiter_started = threading.Event()
    waiter_completed = threading.Event()
    waiter_errors: list[BaseException] = []

    class FakeAttributeList:
        def __init__(self, _sid: ctypes.c_void_p, _handles: tuple[int, ...]) -> None:
            pass

        def __enter__(self) -> FakeAttributeList:
            return self

        def __exit__(self, *_exc: object) -> None:
            pass

    def wait_for_creation_authority() -> None:
        waiter_started.set()
        try:
            windows_module._acquire_process_creation_authority()
        except BaseException as error:
            waiter_errors.append(error)
        else:
            windows_module._PROCESS_CREATION_LOCK.release()
        finally:
            waiter_completed.set()

    waiter: threading.Thread | None = None

    def return_missing_process(
        _self: WindowsProcessLauncher,
        _pipes: Any,
        _attributes: Any,
        _local_app_data: Path,
        information: Any,
    ) -> None:
        nonlocal waiter
        waiter = threading.Thread(target=wait_for_creation_authority)
        waiter.start()
        assert waiter_started.wait(1.0)
        information.process = wintypes.HANDLE()
        information.thread = wintypes.HANDLE(1009)
        information.process_id = 1013
        information.thread_id = 1019

    monkeypatch.setattr(
        _AppContainerProfile,
        "create",
        classmethod(lambda _cls, _recovery_roots: profile),
    )
    monkeypatch.setattr(windows_module, "_Job", lambda _policy: job)
    monkeypatch.setattr(_PipeSet, "create", classmethod(lambda _cls: pipes))
    monkeypatch.setattr(windows_module, "_AttributeList", FakeAttributeList)
    monkeypatch.setattr(
        WindowsProcessLauncher,
        "_create_suspended_process",
        return_missing_process,
    )
    monkeypatch.setattr(windows_module, "_close_raw_handle", lambda _handle: True)
    monkeypatch.setattr(windows_module, "_PROCESS_CREATION_QUARANTINED", False)
    monkeypatch.setattr(windows_module, "_FAILED_LAUNCH_QUARANTINE", [])

    with pytest.raises(WindowsLaunchError) as raised:
        WindowsProcessLauncher(_launch_config(worker_test_resources)).launch(_launch_request())

    assert waiter_completed.wait(2.0)
    assert waiter is not None
    waiter.join(timeout=1.0)
    assert not waiter.is_alive()
    assert raised.value.code is WindowsLaunchErrorCode.CLEANUP_FAILED
    assert raised.value.context == "launch.cleanup"
    assert len(waiter_errors) == 1
    assert isinstance(waiter_errors[0], WindowsLaunchError)
    waiter_error = waiter_errors[0]
    assert waiter_error.code is WindowsLaunchErrorCode.CLEANUP_FAILED
    assert waiter_error.context == "process_creation.quarantine"
    assert len(windows_module._FAILED_LAUNCH_QUARANTINE) == 1
    retained = windows_module._FAILED_LAUNCH_QUARANTINE[0]
    assert retained.process_authority_missing
    assert retained.process is None
    assert not windows_module._drain_failed_launch_quarantine()
    job.assign.assert_not_called()
    profile.close_no_raise.assert_not_called()


def test_failed_launch_cleanup_uses_the_correct_process_tree_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop_job = Mock(return_value=True)
    terminate_process = Mock(return_value=False)
    close_profile = Mock(return_value=True)
    monkeypatch.setattr(windows_module, "_stop_job_tree_no_raise", stop_job)
    monkeypatch.setattr(windows_module, "_terminate_process_no_raise", terminate_process)
    monkeypatch.setattr(windows_module, "_close_owned_handle_bounded", lambda _handle: True)
    monkeypatch.setattr(windows_module, "_close_profile_bounded", close_profile)

    assigned_profile = cast(Any, SimpleNamespace())
    assigned = windows_module._FailedLaunchCleanup(
        cast(Any, SimpleNamespace(handle=_OwnedHandle(839))),
        _OwnedHandle(853),
        assigned_profile,
        True,
    )
    windows_module._FAILED_LAUNCH_QUARANTINE[:] = [assigned]
    assert windows_module._drain_failed_launch_quarantine()
    stop_job.assert_called_once()
    terminate_process.assert_not_called()
    close_profile.assert_called_once_with(assigned_profile)

    stop_job.reset_mock(return_value=True)
    stop_job.return_value = False
    terminate_process.reset_mock(return_value=True)
    terminate_process.return_value = True
    close_profile.reset_mock(return_value=True)
    unknown_profile = cast(Any, SimpleNamespace())
    unknown = windows_module._FailedLaunchCleanup(
        cast(Any, SimpleNamespace(handle=_OwnedHandle(857))),
        _OwnedHandle(859),
        unknown_profile,
        True,
    )
    windows_module._FAILED_LAUNCH_QUARANTINE[:] = [unknown]
    assert not windows_module._drain_failed_launch_quarantine()
    terminate_process.assert_called_once()
    close_profile.assert_not_called()
    assert windows_module._FAILED_LAUNCH_QUARANTINE == [unknown]

    windows_module._FAILED_LAUNCH_QUARANTINE.clear()
    stop_job.reset_mock(return_value=True)
    terminate_process.reset_mock(return_value=True)
    terminate_process.return_value = True
    close_profile.reset_mock(return_value=True)
    unassigned_profile = cast(Any, SimpleNamespace())
    unassigned = windows_module._FailedLaunchCleanup(
        cast(Any, SimpleNamespace(handle=_OwnedHandle(863))),
        _OwnedHandle(877),
        unassigned_profile,
        False,
    )
    windows_module._FAILED_LAUNCH_QUARANTINE[:] = [unassigned]
    assert windows_module._drain_failed_launch_quarantine()
    stop_job.assert_not_called()
    terminate_process.assert_called_once()
    close_profile.assert_called_once_with(unassigned_profile)


def test_final_empty_job_observation_wins_over_termination_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminate = Mock(return_value=False)
    monkeypatch.setattr(windows_module, "_job_active_processes_no_raise", lambda _job: 1)
    monkeypatch.setattr(windows_module, "_terminate_job_no_raise", terminate)
    monkeypatch.setattr(
        windows_module,
        "_wait_for_empty_job_no_raise",
        lambda _job, _timeout: True,
    )

    assert windows_module._stop_job_tree_no_raise(cast(Any, SimpleNamespace()), 1.0)
    terminate.assert_called_once()


_WINDOWS_PROCESS_ENTRY_POINTS = frozenset(
    {
        "CreateProcess",
        "CreateProcessA",
        "CreateProcessW",
        "CreateProcessAsUserA",
        "CreateProcessAsUserW",
        "CreateProcessWithLogonA",
        "CreateProcessWithLogonW",
        "CreateProcessWithTokenW",
        "NtCreateUserProcess",
        "RtlCreateUserProcess",
        "ShellExecuteA",
        "ShellExecuteW",
        "ShellExecuteExA",
        "ShellExecuteExW",
        "WinExec",
    }
)
_PYTHON_PROCESS_ENTRY_POINTS = frozenset(
    {
        "create_subprocess_exec",
        "create_subprocess_shell",
        "popen",
        "posix_spawn",
        "posix_spawnp",
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnlpe",
        "spawnv",
        "spawnve",
        "spawnvp",
        "spawnvpe",
        "startfile",
        "system",
        "ProcessPoolExecutor",
    }
)


def _proven_qlocale_system_attributes(tree: ast.Module) -> frozenset[int]:
    """Recognize only an unshadowed, direct, zero-argument Qt locale query."""
    nodes = tuple(ast.walk(tree))
    bindings: list[tuple[ast.Import | ast.ImportFrom, ast.alias]] = []
    for node in nodes:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if (alias.asname or alias.name.partition(".")[0]) == "QLocale":
                    bindings.append((node, alias))
    if len(bindings) != 1:
        return frozenset()
    statement, alias = bindings[0]
    if (
        not isinstance(statement, ast.ImportFrom)
        or statement.level != 0
        or statement not in tree.body
        or statement.module != "PySide6.QtCore"
        or alias.name != "QLocale"
        or alias.asname is not None
    ):
        return frozenset()
    dynamic_names = {
        "globals",
        "locals",
        "vars",
        "exec",
        "eval",
        "setattr",
        "delattr",
        "__import__",
        "__builtins__",
    }
    named_bindings = (
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.ClassDef,
        ast.ExceptHandler,
        ast.MatchAs,
        ast.MatchStar,
        ast.TypeVar,
        ast.TypeVarTuple,
        ast.ParamSpec,
    )
    parents = {child: parent for parent in nodes for child in ast.iter_child_nodes(parent)}
    for node in nodes:
        if isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names):
            return frozenset()
        if isinstance(node, ast.Import) and any(alias.name == "builtins" for alias in node.names):
            return frozenset()
        if isinstance(node, ast.ImportFrom) and node.module == "builtins":
            return frozenset()
        if isinstance(node, ast.Name) and (
            (node.id == "QLocale" and isinstance(node.ctx, (ast.Store, ast.Del)))
            or node.id in dynamic_names
        ):
            return frozenset()
        if isinstance(node, ast.Name) and node.id == "QLocale" and isinstance(node.ctx, ast.Load):
            parent = parents.get(node)
            direct_constructor = isinstance(parent, ast.Call) and parent.func is node
            direct_class_method = False
            if isinstance(parent, ast.Attribute) and parent.value is node:
                call = parents.get(parent)
                if isinstance(call, ast.Call) and call.func is parent and not call.keywords:
                    direct_class_method = (parent.attr == "system" and not call.args) or (
                        parent.attr == "setDefault" and len(call.args) == 1
                    )
            if not (direct_constructor or direct_class_method):
                return frozenset()
        if isinstance(node, ast.arg) and node.arg == "QLocale":
            return frozenset()
        if isinstance(node, named_bindings) and node.name == "QLocale":
            return frozenset()
        if isinstance(node, ast.MatchMapping) and node.rest == "QLocale":
            return frozenset()
        if isinstance(node, (ast.Global, ast.Nonlocal)) and "QLocale" in node.names:
            return frozenset()
        if isinstance(node, ast.Constant) and node.value == "QLocale":
            return frozenset()
        if isinstance(node, ast.Attribute) and node.attr in {
            "__globals__",
            "f_globals",
            "__dict__",
        }:
            return frozenset()
        if isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(
            node.ctx, (ast.Store, ast.Del)
        ):
            if isinstance(node, ast.Attribute) and node.attr == "QLocale":
                return frozenset()
            if any(
                isinstance(child, ast.Name) and child.id == "QLocale"
                for child in ast.walk(node.value)
            ):
                return frozenset()
    return frozenset(
        id(node.func)
        for node in nodes
        if isinstance(node, ast.Call)
        and not node.args
        and not node.keywords
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "system"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "QLocale"
    )


def _process_creation_guard_findings(
    source: str,
    *,
    path: Path,
    broker: Path,
    subprocess_authorities: frozenset[Path] = frozenset(),
) -> tuple[list[str], set[str]]:
    findings: list[str] = []
    broker_subprocess_calls: set[str] = set()
    admitted_subprocess_modules = {broker, *subprocess_authorities}
    tree = ast.parse(source, filename=os.fspath(path))
    proven_locale_queries = _proven_qlocale_system_attributes(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    if path not in admitted_subprocess_modules or alias.asname is not None:
                        findings.append("subprocess import")
                elif alias.name == "_winapi" or alias.name.startswith("multiprocessing"):
                    findings.append(f"{alias.name} import")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imported = {alias.name for alias in node.names}
            if module == "subprocess" or module == "_winapi":
                findings.append(f"{module} from-import")
            elif module == "multiprocessing" or module.startswith("multiprocessing."):
                findings.append("multiprocessing from-import")
            elif module == "os" and imported & _PYTHON_PROCESS_ENTRY_POINTS:
                findings.append("os process from-import")
            elif module == "asyncio" and imported & _PYTHON_PROCESS_ENTRY_POINTS:
                findings.append("asyncio process from-import")
            elif module == "concurrent.futures" and "ProcessPoolExecutor" in imported:
                findings.append("process pool from-import")
        elif isinstance(node, ast.Attribute):
            if node.attr in _WINDOWS_PROCESS_ENTRY_POINTS:
                if path != broker or node.attr != "CreateProcessW":
                    findings.append(f"Windows process API {node.attr}")
            elif node.attr in _PYTHON_PROCESS_ENTRY_POINTS:
                if id(node) not in proven_locale_queries:
                    findings.append(f"Python process API {node.attr}")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in _WINDOWS_PROCESS_ENTRY_POINTS | _PYTHON_PROCESS_ENTRY_POINTS
        ):
            findings.append(f"dynamic process API {node.value}")
        if (
            path in admitted_subprocess_modules
            and isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
        ):
            broker_subprocess_calls.add(node.func.attr)
    return findings, broker_subprocess_calls


def test_production_windows_process_creation_has_only_locked_authorities() -> None:
    source_root = _REPOSITORY_ROOT / "src" / "paperworkshell"
    broker = source_root / "worker" / "client" / "_windows.py"
    subprocess_authorities = frozenset(
        {
            source_root / "pdf" / "qpdf_checker.py",
            source_root / "ocr" / "ocrmypdf_adapter.py",
            source_root / "ocr" / "tesseract_runner.py",
            source_root / "worker" / "process_creation_policy.py",
        }
    )
    expected_calls = {
        broker: {"list2cmdline"},
        source_root / "pdf" / "qpdf_checker.py": {"Popen"},
        source_root / "ocr" / "ocrmypdf_adapter.py": set(),
        source_root / "ocr" / "tesseract_runner.py": {
            "CalledProcessError",
            "CompletedProcess",
            "Popen",
            "TimeoutExpired",
        },
        source_root / "worker" / "process_creation_policy.py": {"list2cmdline"},
    }
    findings: list[tuple[Path, str]] = []
    subprocess_calls_by_module: dict[Path, set[str]] = {}
    for path in source_root.rglob("*.py"):
        path_findings, subprocess_calls = _process_creation_guard_findings(
            path.read_text(encoding="utf-8"),
            path=path,
            broker=broker,
            subprocess_authorities=subprocess_authorities,
        )
        findings.extend((path, finding) for finding in path_findings)
        if path in expected_calls:
            subprocess_calls_by_module[path] = subprocess_calls

    assert not findings
    assert subprocess_calls_by_module == expected_calls


@pytest.mark.parametrize(
    "source",
    (
        "import os as operating_system\noperating_system.startfile('x')\n",
        "from os import spawnv as launch\nlaunch(0, 'x', ())\n",
        "import _winapi as native\nnative.CreateProcess('x')\n",
        "kernel32.CreateProcessAsUserW()\n",
        "shell32.ShellExecuteExW()\n",
        "from asyncio import create_subprocess_exec as launch\n",
        "from concurrent.futures import ProcessPoolExecutor as Pool\n",
        "getattr(kernel32, 'CreateProcessW')\n",
    ),
)
def test_process_creation_guard_rejects_alternate_entry_points(source: str) -> None:
    path = Path("synthetic_process_entry.py")
    findings, _calls = _process_creation_guard_findings(
        source,
        path=path,
        broker=Path("broker.py"),
    )
    assert findings


def test_process_creation_waiter_rechecks_quarantine_after_lock_release() -> None:
    lock = windows_module._PROCESS_CREATION_LOCK
    assert lock.acquire(timeout=1.0)
    windows_module._PROCESS_CREATION_QUARANTINED = False
    completed = threading.Event()
    errors: list[BaseException] = []

    def wait_for_authority() -> None:
        try:
            windows_module._acquire_process_creation_authority()
        except BaseException as error:
            errors.append(error)
        else:
            lock.release()
        finally:
            completed.set()

    waiter = threading.Thread(target=wait_for_authority)
    waiter.start()
    try:
        windows_module._PROCESS_CREATION_QUARANTINED = True
    finally:
        lock.release()
    assert completed.wait(2.0)
    waiter.join(timeout=1.0)
    assert not waiter.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], WindowsLaunchError)
    error = errors[0]
    assert error.code is WindowsLaunchErrorCode.CLEANUP_FAILED
    assert error.context == "process_creation.quarantine"

    assert lock.acquire(timeout=1.0)
    windows_module._PROCESS_CREATION_QUARANTINED = False
    lock.release()


def test_launcher_holds_broker_lock_until_child_handles_are_safe(
    monkeypatch: pytest.MonkeyPatch,
    worker_test_resources: WorkerTestResources,
) -> None:
    original_create = _PipeSet.create
    pipes_created = threading.Event()
    allow_launch = threading.Event()
    competitor_acquired = threading.Event()
    captured: list[_PipeSet] = []
    launched: list[WindowsWorkerProcess] = []
    failures: list[BaseException] = []

    def blocked_create(_cls: type[_PipeSet]) -> _PipeSet:
        pipes = original_create()
        captured.append(pipes)
        pipes_created.set()
        if not allow_launch.wait(10.0):
            raise RuntimeError("synthetic broker race timeout")
        return pipes

    monkeypatch.setattr(_PipeSet, "create", classmethod(blocked_create))

    def launch_worker() -> None:
        try:
            launched.append(
                WindowsProcessLauncher(_launch_config(worker_test_resources)).launch(
                    _launch_request()
                )
            )
        except BaseException as error:
            failures.append(error)

    def compete_for_creation() -> None:
        acquired = False
        try:
            windows_module._acquire_process_creation_authority()
            acquired = True
            assert captured
            assert all(
                handle._value == 0
                for handle in (
                    captured[0].child_read,
                    captured[0].child_write,
                    captured[0].bootstrap_read,
                )
            )
            competitor_acquired.set()
        except BaseException as error:
            failures.append(error)
        finally:
            if acquired:
                windows_module._PROCESS_CREATION_LOCK.release()

    launcher_thread = threading.Thread(target=launch_worker)
    competitor_thread = threading.Thread(target=compete_for_creation)
    launcher_thread.start()
    try:
        assert pipes_created.wait(60.0)
        competitor_thread.start()
        assert not competitor_acquired.wait(0.2)
    finally:
        allow_launch.set()
    launcher_thread.join(timeout=60.0)
    competitor_thread.join(timeout=15.0)
    assert not launcher_thread.is_alive()
    assert not competitor_thread.is_alive()
    assert competitor_acquired.is_set()
    assert not failures
    assert len(launched) == 1
    launched[0].close()


def test_launch_config_rejects_reparse_indirection(
    tmp_path: Path,
    worker_test_resources: WorkerTestResources,
) -> None:
    target = tmp_path / "trusted-target"
    target.mkdir()
    bootstrap = target / "bootstrap.py"
    bootstrap.write_text("raise SystemExit(0)\n", encoding="utf-8")
    redirected = tmp_path / "redirected"
    try:
        redirected.symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Windows directory-link creation is unavailable: {type(error).__name__}")

    with pytest.raises(WindowsLaunchError) as raised:
        WindowsLaunchConfig(
            python_executable=worker_test_resources.python_executable,
            bootstrap_script=redirected / "bootstrap.py",
            runtime_roots=(worker_test_resources.runtime_root,),
            code_roots=(redirected,),
            working_directory=redirected,
        )
    assert raised.value.code is WindowsLaunchErrorCode.CONFIGURATION_INVALID
    assert raised.value.context == "config.reparse"


@pytest.mark.parametrize(
    "source",
    (
        "from PySide6.QtCore import QLocale\nvalue = QLocale.system().name()\n",
        "from PySide6.QtCore import QLocale\ndef current():\n    return QLocale.system()\n",
    ),
)
def test_process_creation_guard_admits_proven_qlocale_query(source: str) -> None:
    findings, _calls = _process_creation_guard_findings(
        source, path=Path("synthetic_locale.py"), broker=Path("broker.py")
    )
    assert not findings


@pytest.mark.parametrize(
    "source",
    (
        "import os as QLocale\nQLocale.system()\n",
        "from untrusted import QLocale\nQLocale.system()\n",
        "from .PySide6.QtCore import QLocale\nQLocale.system()\n",
        "from PySide6.QtCore import QLocale as locale\nlocale.system()\n",
        "from PySide6.QtCore import QLocale as QLocale\nQLocale.system()\n",
        "from PySide6.QtCore import QLocale\nfrom other import QLocale\nQLocale.system()\n",
        "from PySide6.QtCore import QLocale\nfrom other import *\nQLocale.system()\n",
        "from PySide6.QtCore import QLocale\nfrom PySide6.QtCore import QLocale\nQLocale.system()\n",
        "from PySide6.QtCore import QLocale\nimport QLocale.helpers\nQLocale.system()\n",
        "from PySide6.QtCore import QLocale\nQLocale = other\nQLocale.system()\n",
        "from PySide6.QtCore import QLocale\ndel QLocale\nQLocale.system()\n",
        "from PySide6.QtCore import QLocale\ndef invoke(QLocale):\n    return QLocale.system()\n",
        "from PySide6.QtCore import QLocale\nclass QLocale:\n    pass\nQLocale.system()\n",
        "from PySide6.QtCore import QLocale\ndef QLocale():\n    pass\nQLocale.system()\n",
        "from PySide6.QtCore import QLocale\nfor QLocale in values:\n    QLocale.system()\n",
        "from PySide6.QtCore import QLocale\nvalues = [QLocale.system() for QLocale in items]\n",
        "from PySide6.QtCore import QLocale\nwith context() as QLocale:\n    QLocale.system()\n",
        "from PySide6.QtCore import QLocale\ntry:\n    pass\nexcept Exception as QLocale:\n    QLocale.system()\n",
        "from PySide6.QtCore import QLocale\nmatch value:\n    case QLocale:\n        pass\nQLocale.system()\n",
        "from PySide6.QtCore import QLocale\ndef invoke[QLocale]():\n    return QLocale.system()\n",
        "from PySide6.QtCore import QLocale\nQLocale.system = replacement\nQLocale.system()\n",
        "from PySide6.QtCore import QLocale\nQLocale.other = replacement\nQLocale.system()\n",
        "from PySide6.QtCore import QLocale\nQLocale.__dict__[key] = replacement\nQLocale.system()\n",
        "from PySide6.QtCore import QLocale\nQLocale.system.__code__ = replacement\nQLocale.system()\n",
        "from PySide6.QtCore import QLocale\nsetattr(QLocale, key, replacement)\nQLocale.system()\n",
        "from PySide6.QtCore import QLocale\nglobals()['Q' + 'Locale'] = other\nQLocale.system()\n",
        "from PySide6.QtCore import QLocale\nquery = QLocale.system\n",
        "from PySide6.QtCore import QLocale\nQLocale.system(command)\n",
        "from PySide6.QtCore import QLocale\nQLocale.system(*args)\n",
        "from PySide6.QtCore import QLocale\nQLocale.system(**options)\n",
        "from PySide6.QtCore import QLocale\nalias = QLocale\nalias.system()\n",
        "from PySide6.QtCore import QLocale\ngetattr(QLocale, 'system')()\n",
        "from PySide6.QtCore import QLocale\nfactory().system()\n",
        "from PySide6.QtCore import QLocale\nimport os\nos.system(command)\n",
        "from PySide6.QtCore import QLocale\ngetattr(os, 'system')(command)\n",
    ),
)
def test_process_creation_guard_rejects_unproved_locale_receivers(source: str) -> None:
    findings, _calls = _process_creation_guard_findings(
        source, path=Path("synthetic_locale.py"), broker=Path("broker.py")
    )
    assert findings


def test_process_creation_guard_admits_direct_locale_constructor_and_default() -> None:
    source = (
        "from PySide6.QtCore import QLocale\n"
        "native = QLocale('en')\n"
        "QLocale.setDefault(native)\n"
        "value = QLocale.system().name()\n"
    )
    findings, _calls = _process_creation_guard_findings(
        source, path=Path("synthetic_locale.py"), broker=Path("broker.py")
    )
    assert not findings


@pytest.mark.parametrize(
    "source",
    (
        "def bind():\n    from PySide6.QtCore import QLocale\nQLocale.system()\n",
        "def current():\n    from PySide6.QtCore import QLocale\n    return QLocale.system()\n",
        "class Binding:\n    from PySide6.QtCore import QLocale\nQLocale.system()\n",
        "if condition:\n    from PySide6.QtCore import QLocale\nQLocale.system()\n",
        "if True:\n    from PySide6.QtCore import QLocale\nQLocale.system()\n",
        "from PySide6.QtCore import QLocale\nunknown_mutator(QLocale)\nQLocale.system()\n",
        "from PySide6.QtCore import QLocale\nalias = QLocale\nQLocale.system()\n",
        "from PySide6.QtCore import QLocale\ncontainer = [QLocale]\nQLocale.system()\n",
        "from PySide6.QtCore import QLocale\ndef expose():\n    return QLocale\nQLocale.system()\n",
        "from PySide6.QtCore import QLocale\ngetattr(QLocale, key)\nQLocale.system()\n",
        "from PySide6.QtCore import QLocale\n__builtins__['globals']()['Q' + 'Locale'] = other\nQLocale.system()\n",
        "from PySide6.QtCore import QLocale\nimport sys\nsys.modules[__name__].QLocale = other\nQLocale.system()\n",
        "from PySide6.QtCore import QLocale\nimport sys\nsys.modules[__name__].__dict__[key] = other\nQLocale.system()\n",
        "from PySide6.QtCore import QLocale\nimport sys\nsys.modules[__name__].__dict__['Q' + 'Locale'] = other\nQLocale.system()\n",
    ),
)
def test_process_creation_guard_rejects_scope_and_value_escapes(source: str) -> None:
    findings, _calls = _process_creation_guard_findings(
        source, path=Path("synthetic_locale.py"), broker=Path("broker.py")
    )
    assert findings

"""Native contracts for retained immutable access and fresh parser datastores.

These tests use disposable owned resources and a private ACL journal/mutex.
The probe is a fixed test executable path, not a production RPC capability.
Do not run this module concurrently with installed-application measurements.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from ctypes import wintypes
from dataclasses import replace
from pathlib import Path
from secrets import token_hex
from uuid import UUID, uuid4

import pytest

from paperworkshell.worker.client import _windows as windows_module
from paperworkshell.worker.client._acl_recovery import AclRecoveryJournal
from paperworkshell.worker.client._windows import (
    RuntimeAccessSnapshot,
    WindowsLaunchConfig,
    WindowsLaunchError,
    WindowsLaunchErrorCode,
    WindowsProcessLauncher,
    WindowsWorkerProcess,
)
from paperworkshell.worker.client.client import WorkerClient
from paperworkshell.worker.client.models import WorkerLifecyclePhase, WorkerTimeoutPolicy
from paperworkshell.worker.client.process import TransportReceiveKind, WorkerLaunchRequest
from paperworkshell.worker.protocol.models import (
    NOOP_CAPABILITY,
    MessageType,
    OperationOutcome,
    ProtocolErrorPayload,
)
from tests.integration.test_worker_windows_containment import (
    _assert_profile_removed,
    _dacl_sddl,
    _launch_config,
    _launch_request,
    _negotiate,
    _noop_operation,
    _pump_until,
)
from tests.worker_resources import (
    WorkerTestResources,
    create_worker_test_owned_directory,
    create_worker_test_recovery_path,
)

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="Retained runtime access requires native Windows AppContainer evidence"
)


@pytest.fixture(autouse=True)
def _private_recovery_authority(
    monkeypatch: pytest.MonkeyPatch,
    worker_test_resources: WorkerTestResources,
) -> Iterator[Path]:
    journal = create_worker_test_recovery_path(worker_test_resources, "retained-runtime")
    monkeypatch.setattr(windows_module, "_acl_recovery_directory_path", lambda: journal)
    monkeypatch.setattr(
        windows_module,
        "_ACL_MUTATION_MUTEX_NAME",
        f"Global\\PaperWorkShell.Test.RetainedRuntime.{token_hex(12)}",
    )
    yield journal
    if journal.exists():
        # Never delete a live/uncertain record merely to make fixture cleanup pass.
        try:
            clean = AclRecoveryJournal(journal).inventory() == ()
        except BaseException:
            worker_test_resources.cleanup_guard.cleanup_safe = False
            raise
        if not clean:
            worker_test_resources.cleanup_guard.cleanup_safe = False
        assert clean


def _client(launcher: WindowsProcessLauncher) -> WorkerClient:
    return WorkerClient(
        launcher,
        _launch_request().resource_policy,
        WorkerTimeoutPolicy(
            handshake_seconds=15.0,
            cancellation_grace_seconds=5.0,
            shutdown_grace_seconds=10.0,
        ),
        required_capabilities=(NOOP_CAPABILITY,),
    )


def _complete_noop(client: WorkerClient, case_id: UUID) -> tuple[int, UUID, UUID]:
    generation = client.start(case_id)
    _, ready = _pump_until(client, WorkerLifecyclePhase.READY)
    assert ready.pid is not None and ready.session_id is not None
    assert ready.worker_instance_id is not None
    identity = (generation, ready.session_id, ready.worker_instance_id)
    client.start_operation(_noop_operation())
    _, completed = _pump_until(client, WorkerLifecyclePhase.READY)
    assert completed.last_result is not None
    assert completed.last_result.outcome is OperationOutcome.SUCCEEDED
    client.terminate()
    assert client.query().phase is WorkerLifecyclePhase.STOPPED
    assert client.query().pid is None
    return identity


def _capture_launches(
    monkeypatch: pytest.MonkeyPatch, launcher: WindowsProcessLauncher
) -> list[WindowsWorkerProcess]:
    launched: list[WindowsWorkerProcess] = []
    original = WindowsProcessLauncher.launch

    def capture(self: WindowsProcessLauncher, request: WorkerLaunchRequest) -> WindowsWorkerProcess:
        process = original(self, request)
        if self is launcher:
            launched.append(process)
        return process

    monkeypatch.setattr(WindowsProcessLauncher, "launch", capture)
    return launched


def test_successful_generations_keep_only_one_static_lease_and_fresh_jobs(
    worker_test_resources: WorkerTestResources,
    monkeypatch: pytest.MonkeyPatch,
    _private_recovery_authority: Path,
) -> None:
    launcher = WindowsProcessLauncher(
        _launch_config(worker_test_resources), retain_runtime_access=True
    )
    client = _client(launcher)
    launched = _capture_launches(monkeypatch, launcher)
    case_id = uuid4()
    try:
        first = _complete_noop(client, case_id)
        entries = AclRecoveryJournal(_private_recovery_authority).inventory()
        assert len(entries) == 1
        first_entry = entries[0]
        assert launcher.runtime_access_snapshot.retained
        assert not launcher.runtime_access_snapshot.active_generation
        assert not launcher.runtime_access_snapshot.poisoned
        assert launcher.runtime_access_snapshot.case_id == case_id
        assert len(launched) == 1 and launched[0]._tree_confirmed
        _assert_profile_removed(launched[0]._profile.local_app_data)

        second = _complete_noop(client, case_id)
        assert second[0] == first[0] + 1
        assert second[1] != first[1] and second[2] != first[2]
        assert len(launched) == 2
        assert launched[0] is not launched[1]
        assert launched[0]._job is not launched[1]._job
        assert all(process._tree_confirmed and process._containment_closed for process in launched)
        entries = AclRecoveryJournal(_private_recovery_authority).inventory()
        assert len(entries) == 1
        assert entries[0].profile_id == first_entry.profile_id
        assert entries[0].sid == first_entry.sid
        assert entries[0].objects == first_entry.objects
        _assert_profile_removed(launched[1]._profile.local_app_data)
    finally:
        client.terminate()
        launcher.release_case_resources()
    assert launcher.runtime_access_snapshot == RuntimeAccessSnapshot()


def test_default_launcher_still_revokes_each_one_shot_generation(
    worker_test_resources: WorkerTestResources,
    monkeypatch: pytest.MonkeyPatch,
    _private_recovery_authority: Path,
) -> None:
    launcher = WindowsProcessLauncher(_launch_config(worker_test_resources))
    client = _client(launcher)
    launched = _capture_launches(monkeypatch, launcher)
    try:
        _complete_noop(client, uuid4())
        assert len(launched) == 1 and launched[0]._tree_confirmed
        assert not launcher.runtime_access_snapshot.retained
        assert AclRecoveryJournal(_private_recovery_authority).inventory() == ()
        _assert_profile_removed(launched[0]._profile.local_app_data)
    finally:
        client.terminate()
        launcher.release_case_resources()


def test_two_fixed_launchers_share_static_authority_but_never_a_live_generation(
    worker_test_resources: WorkerTestResources,
    monkeypatch: pytest.MonkeyPatch,
    _private_recovery_authority: Path,
) -> None:
    config = _launch_config(worker_test_resources)
    first = WindowsProcessLauncher(config, retain_runtime_access=True)
    authority = first.runtime_access_authority
    assert authority is not None
    second = WindowsProcessLauncher(config, runtime_access=authority)
    assert second.runtime_access_authority is authority
    first_client, second_client = _client(first), _client(second)
    first_processes = _capture_launches(monkeypatch, first)
    second_processes = _capture_launches(monkeypatch, second)
    case_id = uuid4()
    try:
        first_client.start(case_id)
        _, ready = _pump_until(first_client, WorkerLifecyclePhase.READY)
        assert ready.session_id is not None and ready.worker_instance_id is not None
        first_identity = (ready.session_id, ready.worker_instance_id)
        assert first.runtime_access_snapshot.active_generation
        active_entry = AclRecoveryJournal(_private_recovery_authority).inventory()
        assert len(active_entry) == 1
        with pytest.raises(WindowsLaunchError) as refused:
            second.launch(replace(_launch_request(), case_id=case_id))
        assert refused.value.code is WindowsLaunchErrorCode.CONFIGURATION_INVALID
        assert first.runtime_access_snapshot.active_generation
        assert second_processes == []
        first_client.start_operation(_noop_operation())
        _, completed = _pump_until(first_client, WorkerLifecyclePhase.READY)
        assert completed.last_result is not None
        assert completed.last_result.outcome is OperationOutcome.SUCCEEDED
        first_client.terminate()

        second_identity = _complete_noop(second_client, case_id)
        third_identity = _complete_noop(first_client, case_id)
        assert len({first_identity[0], second_identity[1], third_identity[1]}) == 3
        assert len({first_identity[1], second_identity[2], third_identity[2]}) == 3
        assert len(first_processes) == 2 and len(second_processes) == 1
        processes = (*first_processes, *second_processes)
        assert len({id(process._job) for process in processes}) == 3
        assert all(process._tree_confirmed and process._containment_closed for process in processes)
        for process in processes:
            _assert_profile_removed(process._profile.local_app_data)
        after = AclRecoveryJournal(_private_recovery_authority).inventory()
        assert len(after) == 1
        assert after[0].sid == active_entry[0].sid
        assert after[0].profile_id == active_entry[0].profile_id
        assert after[0].objects == active_entry[0].objects
        assert first.runtime_access_snapshot == second.runtime_access_snapshot
    finally:
        first_client.terminate()
        second_client.terminate()
        first.release_case_resources()
    assert first.runtime_access_snapshot == RuntimeAccessSnapshot()
    assert second.runtime_access_snapshot == RuntimeAccessSnapshot()


def test_cancelled_generation_retires_its_static_identity_before_a_fresh_retry(
    worker_test_resources: WorkerTestResources,
    _private_recovery_authority: Path,
) -> None:
    launcher = WindowsProcessLauncher(
        _launch_config(worker_test_resources), retain_runtime_access=True
    )
    client = _client(launcher)
    case_id = uuid4()
    try:
        _complete_noop(client, case_id)
        before = AclRecoveryJournal(_private_recovery_authority).inventory()
        assert len(before) == 1
        client.start(case_id)
        _pump_until(client, WorkerLifecyclePhase.READY)
        client.start_operation(_noop_operation())
        client.cancel()
        _, terminal = _pump_until(client, WorkerLifecyclePhase.READY)
        assert terminal.last_result is not None
        assert terminal.last_result.outcome is OperationOutcome.CANCELLED
        client.terminate()
        assert not launcher.runtime_access_snapshot.retained
        assert AclRecoveryJournal(_private_recovery_authority).inventory() == ()
        _complete_noop(client, case_id)
        after = AclRecoveryJournal(_private_recovery_authority).inventory()
        assert len(after) == 1
        assert after[0].profile_id != before[0].profile_id
        assert after[0].sid != before[0].sid
    finally:
        client.terminate()
        launcher.release_case_resources()


def test_malformed_rpc_to_real_host_cannot_keep_runtime_access(
    worker_test_resources: WorkerTestResources,
    _private_recovery_authority: Path,
) -> None:
    launcher = WindowsProcessLauncher(
        _launch_config(worker_test_resources), retain_runtime_access=True
    )
    request = _launch_request()
    process = launcher.launch(request)
    try:
        peer = _negotiate(process, request)
        process.transport.send_payload(b"{}")
        received = process.transport.receive_payload(10.0)
        assert received.kind is TransportReceiveKind.PAYLOAD and received.payload is not None
        response = peer.accept_inbound(received.payload)
        assert response.message_type is MessageType.PROTOCOL_ERROR
        assert isinstance(response.payload, ProtocolErrorPayload)
        assert response.payload.error.restart_required
        assert process.wait(10.0) is not None
    finally:
        # No successful-result marker is supplied for a protocol fault.
        process.close()
        launcher.release_case_resources()
    assert process._tree_confirmed
    assert not launcher.runtime_access_snapshot.retained
    assert AclRecoveryJournal(_private_recovery_authority).inventory() == ()


def test_retained_lease_rejects_case_switch_until_explicit_release(
    worker_test_resources: WorkerTestResources,
    _private_recovery_authority: Path,
) -> None:
    launcher = WindowsProcessLauncher(
        _launch_config(worker_test_resources), retain_runtime_access=True
    )
    client = _client(launcher)
    first_case, other_case = uuid4(), uuid4()
    try:
        _complete_noop(client, first_case)
        first = AclRecoveryJournal(_private_recovery_authority).inventory()[0]
        with pytest.raises(WindowsLaunchError) as refused:
            launcher.launch(replace(_launch_request(), case_id=other_case))
        assert refused.value.code is WindowsLaunchErrorCode.CONFIGURATION_INVALID
        assert launcher.runtime_access_snapshot.case_id == first_case
        assert not launcher.runtime_access_snapshot.active_generation
        launcher.release_case_resources()
        _complete_noop(client, other_case)
        other = AclRecoveryJournal(_private_recovery_authority).inventory()[0]
        assert other.sid != first.sid
        assert launcher.runtime_access_snapshot.case_id == other_case
    finally:
        client.terminate()
        launcher.release_case_resources()


_PROFILE_PROBE = r"""
import ctypes
import json
import os
import sys
import winreg
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, SOURCE_ROOT)
from paperworkshell.worker._windows_runtime import read_bootstrap_from_standard_error, verify_runtime_isolation
from paperworkshell.worker.protocol.bootstrap import decode_bootstrap_grant
from paperworkshell.worker.protocol.framing import read_frame, write_frame

grant = decode_bootstrap_grant(read_bootstrap_from_standard_error())
verify_runtime_isolation()
reader = getattr(sys.stdin.buffer, 'raw', sys.stdin.buffer)
writer = getattr(sys.stdout.buffer, 'raw', sys.stdout.buffer)
command = read_frame(reader)
assert command in {b'seed', b'inspect'}
root = Path(os.environ['LOCALAPPDATA'])
temp = Path(os.environ['TEMP'])
cache = Path(str(sys.pycache_prefix))
temp_marker = temp / 'pws-retained-generation-sentinel.tmp'
root_marker = root / 'pws-retained-generation-sentinel.bin'
get_registry = ctypes.WinDLL('userenv', use_last_error=True).GetAppContainerRegistryLocation
get_registry.argtypes = (wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
get_registry.restype = ctypes.c_long
key = wintypes.HANDLE()
assert get_registry(winreg.KEY_READ | winreg.KEY_WRITE, ctypes.byref(key)) == 0
assert key.value
try:
    try:
        winreg.QueryValueEx(key.value, 'PwsRetainedGenerationSentinel')
    except FileNotFoundError:
        prior_registry = False
    else:
        prior_registry = True
    response = {
        'case_id': str(grant.case_id),
        'session_id': str(grant.session_id),
        'worker_id': str(grant.worker_instance_id),
        'prior_temp': temp_marker.exists(),
        'prior_other_profile_file': root_marker.exists(),
        'prior_registry': prior_registry,
        'cache_empty': not tuple(cache.iterdir()),
    }
    try:
        (cache / 'poison.pyc').write_bytes(b'not-trusted-bytecode')
    except PermissionError:
        response['pycache_write_denied'] = True
    else:
        response['pycache_write_denied'] = False
    if command == b'seed':
        temp_marker.write_bytes(b'synthetic-private-temp-sentinel')
        root_marker.write_bytes(b'synthetic-other-profile-sentinel')
        winreg.SetValueEx(key.value, 'PwsRetainedGenerationSentinel', 0, winreg.REG_SZ, 'synthetic-sentinel')
        assert winreg.QueryValueEx(key.value, 'PwsRetainedGenerationSentinel')[0] == 'synthetic-sentinel'
        response['seeded'] = temp_marker.is_file() and root_marker.is_file()
    else:
        response['seeded'] = False
    response['cache_still_empty'] = not tuple(cache.iterdir())
finally:
    winreg.CloseKey(key.value)
write_frame(writer, json.dumps(response, sort_keys=True).encode('ascii'))
assert read_frame(reader) == b'finish'
write_frame(writer, b'finished')
"""


def _profile_probe_config(resources: WorkerTestResources) -> WindowsLaunchConfig:
    code = create_worker_test_owned_directory(resources, "fixed-profile-probe")
    script = code / "probe.py"
    script.write_text(
        "SOURCE_ROOT = " + repr(os.fspath(resources.source_root)) + "\n" + _PROFILE_PROBE,
        encoding="utf-8",
    )
    return WindowsLaunchConfig(
        python_executable=resources.python_executable,
        bootstrap_script=script,
        runtime_roots=(resources.runtime_root,),
        code_roots=(resources.source_root, code),
        working_directory=code,
    )


def _run_profile_probe(
    launcher: WindowsProcessLauncher, request: WorkerLaunchRequest, command: bytes
) -> tuple[dict[str, object], WindowsWorkerProcess]:
    process = launcher.launch(request)
    try:
        process.transport.send_payload(command)
        received = process.transport.receive_payload(15.0)
        assert received.kind is TransportReceiveKind.PAYLOAD and received.payload is not None
        response = json.loads(received.payload)
        assert isinstance(response, dict)
        assert response["case_id"] == str(request.case_id)
        assert response["session_id"] == str(request.session_id)
        assert response["worker_id"] == str(request.worker_instance_id)
        assert response["cache_empty"] is True and response["cache_still_empty"] is True
        assert response["pycache_write_denied"] is True
        process.transport.send_payload(b"finish")
        finished = process.transport.receive_payload(5.0)
        assert finished.kind is TransportReceiveKind.PAYLOAD and finished.payload == b"finished"
        process.terminate_tree()
        assert process._tree_confirmed
        process.allow_runtime_reuse()
    finally:
        process.close()
    return response, process


def test_same_sid_reset_removes_real_registry_and_profile_files_without_warm_bytecode(
    worker_test_resources: WorkerTestResources,
    _private_recovery_authority: Path,
) -> None:
    launcher = WindowsProcessLauncher(
        _profile_probe_config(worker_test_resources), retain_runtime_access=True
    )
    first_request = _launch_request()
    try:
        seeded, first = _run_profile_probe(launcher, first_request, b"seed")
        assert seeded["seeded"] is True
        assert all(
            seeded[key] is False
            for key in ("prior_temp", "prior_other_profile_file", "prior_registry")
        )
        first_sid = windows_module._sid_string(first._profile.sid)
        _assert_profile_removed(first._profile.local_app_data)
        assert len(AclRecoveryJournal(_private_recovery_authority).inventory()) == 1
        second_request = replace(_launch_request(), case_id=first_request.case_id)
        inspected, second = _run_profile_probe(launcher, second_request, b"inspect")
        assert all(
            inspected[key] is False
            for key in ("prior_temp", "prior_other_profile_file", "prior_registry")
        )
        assert windows_module._sid_string(second._profile.sid) == first_sid
        assert second_request.session_id != first_request.session_id
        assert second_request.worker_instance_id != first_request.worker_instance_id
        assert first._job is not second._job
        _assert_profile_removed(second._profile.local_app_data)
    finally:
        launcher.release_case_resources()


def _hold_file_without_delete_sharing(path: Path) -> windows_module._OwnedHandle:
    create = windows_module._kernel32().CreateFileW
    create.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create.restype = wintypes.HANDLE
    value = create(os.fspath(path), 0x80000000, 0x3, None, 3, 0, None)
    assert value not in (None, ctypes.c_void_p(-1).value)
    return windows_module._OwnedHandle(int(value))


def test_held_storage_refuses_reset_and_poison_still_allows_exact_cleanup(
    worker_test_resources: WorkerTestResources, _private_recovery_authority: Path
) -> None:
    approved = create_worker_test_owned_directory(worker_test_resources, "approved-static-code")
    module = approved / "fixed.py"
    module.write_text("VALUE = 1\n", encoding="utf-8")
    before = _dacl_sddl(module)
    context = windows_module._CaseRuntimeAccess((approved,))
    profile = context.acquire(uuid4())
    profile.grant_read_execute((approved,))
    marker = profile.local_app_data / "Temp" / "deliberately-held.tmp"
    marker.write_bytes(b"synthetic-held-file")
    held = _hold_file_without_delete_sharing(marker)
    try:
        with pytest.raises(WindowsLaunchError) as refused:
            context.finish(profile, reusable=True)
        assert refused.value.code is WindowsLaunchErrorCode.CLEANUP_FAILED
        failed = context.snapshot
        assert failed.poisoned and failed.retained
        assert not failed.active_generation
        with pytest.raises(WindowsLaunchError):
            context.acquire(uuid4())
    finally:
        held.close()
        context.retire()
    retired = context.snapshot
    assert retired.poisoned and not retired.retained
    assert _dacl_sddl(module) == before
    assert AclRecoveryJournal(_private_recovery_authority).inventory() == ()
    _assert_profile_removed(profile.local_app_data)


_CRASH_AFTER_RESET = r"""
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from paperworkshell.worker.client import _windows as w
journal, ready, approved = map(Path, sys.argv[2:5])
w._acl_recovery_directory_path = lambda: journal
w._ACL_MUTATION_MUTEX_NAME = sys.argv[5]
profile = w._AppContainerProfile.create((approved,))
profile.grant_read_execute((approved,))
w._reset_profile_storage(profile)
assert profile._deleted and not profile.local_app_data.exists()
ready.write_text(json.dumps({'profile_name': profile.name, 'sid': w._sid_string(profile.sid)}), encoding='ascii')
os._exit(23)
"""


def test_dead_owner_recovery_revokes_static_access_when_profile_was_already_deleted(
    worker_test_resources: WorkerTestResources,
    tmp_path: Path,
    _private_recovery_authority: Path,
) -> None:
    approved = create_worker_test_owned_directory(worker_test_resources, "crash-static-code")
    module = approved / "fixed.py"
    module.write_text("VALUE = 1\n", encoding="utf-8")
    paths = (approved, module)
    before = tuple(_dacl_sddl(path) for path in paths)
    ready = tmp_path / "reset-owner-ready.json"
    script = tmp_path / "fixed-reset-owner.py"
    script.write_text(_CRASH_AFTER_RESET, encoding="utf-8")
    result = subprocess.run(
        (
            sys.executable,
            "-I",
            "-S",
            "-B",
            os.fspath(script),
            os.fspath(worker_test_resources.source_root),
            os.fspath(_private_recovery_authority),
            os.fspath(ready),
            os.fspath(approved),
            windows_module._ACL_MUTATION_MUTEX_NAME,
        ),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30.0,
        check=False,
    )
    assert result.returncode == 23 and ready.is_file()
    evidence = json.loads(ready.read_text(encoding="ascii"))
    entries = AclRecoveryJournal(_private_recovery_authority).inventory()
    assert len(entries) == 1 and entries[0].profile_name == evidence["profile_name"]
    assert entries[0].sid == evidence["sid"]
    assert tuple(_dacl_sddl(path) for path in paths) != before
    recovery = windows_module.recover_worker_acl_access((approved,))
    assert recovery.repaired_leases == 1
    assert tuple(_dacl_sddl(path) for path in paths) == before
    assert AclRecoveryJournal(_private_recovery_authority).inventory() == ()

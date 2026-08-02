"""Live Windows isolation evidence for PWS-0107's remaining threat cases."""

from __future__ import annotations

import ctypes
import json
import os
import select
import socket
import sys
import tempfile
from contextlib import ExitStack
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from secrets import token_hex
from typing import Any, Final, Self
from uuid import uuid4

import pytest

from paperworkshell.worker.client._windows import WindowsLaunchConfig, WindowsProcessLauncher
from paperworkshell.worker.client.process import (
    TransportReceiveKind,
    WorkerLaunchRequest,
    WorkerResourcePolicy,
)
from paperworkshell.worker.protocol.bootstrap import BootstrapGrant, encode_bootstrap_grant
from paperworkshell.worker.protocol.models import SessionCapability

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="PWS-0107 containment targets the supported Windows platform"
)

_REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[2]
_HELPER_ROOT: Final = _REPOSITORY_ROOT / "tests" / "helpers"
_HELPER: Final = _HELPER_ROOT / "pws_worker_isolation_probe.py"
_AMBIENT_CANARY: Final = "PWS_AMBIENT_ENV_CANARY_0107"
_AMBIENT_NAMES: Final = (
    "ALL_PROXY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AZURE_CLIENT_ID",
    "AZURE_CLIENT_SECRET",
    "COMSPEC",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "PATH",
    "PATHEXT",
    "PYTHONPATH",
)

# Bit 17 used to infer ambient-handle absence from raw process-local HANDLE
# numbers. Exact kernel-object identity is now proved directly by the launcher
# test, outside this worker-reported mask.
_INITIAL_REQUIRED_MASK: Final = ((1 << 19) - 1) & ~(1 << 17)
_DESCENDANT_REQUIRED_MASK: Final = (_INITIAL_REQUIRED_MASK & ~(1 << 16)) | (1 << 19)
_RESPONSE_KEYS: Final = frozenset(
    {
        "mask",
        "profile_relations",
        "udp_send_reported",
        "descendant_mask",
        "descendant_profile_relations",
        "descendant_udp_send_reported",
        "descendant_pid",
    }
)
_SYNCHRONIZE: Final = 0x0010_0000
_WAIT_OBJECT_0: Final = 0
_WAIT_TIMEOUT: Final = 258
_HANDLE_FLAG_INHERIT: Final = 1
_DUPLICATE_SAME_ACCESS: Final = 0x0000_0002
_ERROR_INVALID_HANDLE: Final = 6
_REQUIRED_PROFILE_RELATIONS: Final = 0b01011010


@dataclass(slots=True)
class _LocalListeners:
    tcp4: socket.socket
    udp4: socket.socket
    tcp6: socket.socket
    udp6: socket.socket

    @classmethod
    def create(cls, stack: ExitStack) -> Self:
        tcp4 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        udp4 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tcp6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        udp6 = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        listeners = cls(tcp4=tcp4, udp4=udp4, tcp6=tcp6, udp6=udp6)
        for candidate in listeners.all:
            stack.callback(candidate.close)
            candidate.settimeout(2.0)
        tcp6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        udp6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        tcp4.bind(("127.0.0.1", 0))
        tcp4.listen()
        udp4.bind(("127.0.0.1", 0))
        tcp6.bind(("::1", 0))
        tcp6.listen()
        udp6.bind(("::1", 0))
        return listeners

    @property
    def all(self) -> tuple[socket.socket, socket.socket, socket.socket, socket.socket]:
        return self.tcp4, self.udp4, self.tcp6, self.udp6

    @property
    def ports(self) -> tuple[int, int, int, int]:
        return (
            int(self.tcp4.getsockname()[1]),
            int(self.udp4.getsockname()[1]),
            int(self.tcp6.getsockname()[1]),
            int(self.udp6.getsockname()[1]),
        )

    def prove_control_reachability(self) -> None:
        tcp4_port, udp4_port, tcp6_port, udp6_port = self.ports
        with socket.create_connection(("127.0.0.1", tcp4_port), timeout=2.0):
            accepted, _address = self.tcp4.accept()
            accepted.close()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            assert sender.sendto(b"pws-control", ("127.0.0.1", udp4_port)) > 0
        assert self.udp4.recvfrom(64)[0] == b"pws-control"

        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sender:
            sender.settimeout(2.0)
            sender.connect(("::1", tcp6_port))
            accepted, _address = self.tcp6.accept()
            accepted.close()
        with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as sender:
            assert sender.sendto(b"pws-control", ("::1", udp6_port)) > 0
        assert self.udp6.recvfrom(64)[0] == b"pws-control"

    def assert_no_isolated_traffic(self) -> None:
        readable, _writable, _exceptional = select.select(list(self.all), [], [], 1.0)
        assert readable == []


def _launch_config() -> WindowsLaunchConfig:
    source_root = _REPOSITORY_ROOT / "src"
    return WindowsLaunchConfig(
        python_executable=Path(sys.base_prefix) / "python.exe",
        bootstrap_script=_HELPER,
        runtime_roots=(Path(sys.base_prefix),),
        code_roots=(source_root, _HELPER_ROOT),
        working_directory=_HELPER_ROOT,
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


def _create_synthetic_targets(case_root: Path, external_root: Path) -> tuple[Path, ...]:
    targets = (
        case_root / "manifest.json",
        case_root / "journal.jsonl",
        case_root / "originals" / "source.bin",
        case_root / "exports" / "history.json",
        case_root.parent / "sibling-case" / "manifest.json",
        case_root.parent / "sibling-case" / "originals" / "source.bin",
        external_root / "user-temp-source.bin",
    )
    for index, target in enumerate(targets):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f"synthetic-pws-0107-{index}".encode("ascii"))
    return targets


def _encode_control(
    profile_path: Path,
    denied_paths: tuple[Path, ...],
    ports: tuple[int, int, int, int],
) -> bytes:
    return json.dumps(
        {
            "profile_path": os.fspath(profile_path),
            "denied_read_paths": [os.fspath(path) for path in denied_paths],
            "denied_write_paths": [os.fspath(path) for path in denied_paths],
            "ports": list(ports),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _decode_response(payload: bytes) -> tuple[int, int, int, int, int, int, int]:
    try:
        decoded: Any = json.loads(payload)
    except UnicodeDecodeError, json.JSONDecodeError:
        raise AssertionError("the isolation probe returned invalid evidence") from None
    assert type(decoded) is dict
    assert frozenset(decoded) == _RESPONSE_KEYS
    assert all(type(value) is int for value in decoded.values())
    return (
        decoded["mask"],
        decoded["profile_relations"],
        decoded["udp_send_reported"],
        decoded["descendant_mask"],
        decoded["descendant_profile_relations"],
        decoded["descendant_udp_send_reported"],
        decoded["descendant_pid"],
    )


def _create_inheritable_event() -> int:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_event = kernel32.CreateEventW
    create_event.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
    create_event.restype = wintypes.HANDLE
    set_handle_information = kernel32.SetHandleInformation
    set_handle_information.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD)
    set_handle_information.restype = wintypes.BOOL
    handle = create_event(None, True, False, None)
    assert handle
    assert set_handle_information(
        wintypes.HANDLE(handle), _HANDLE_FLAG_INHERIT, _HANDLE_FLAG_INHERIT
    )
    return int(handle)


def _assert_process_did_not_inherit_handles(
    source_handles: tuple[int, ...],
    target_process_handle: int,
) -> None:
    """Prove object absence without treating process-local HANDLE values as identities."""

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    current_process = kernel32.GetCurrentProcess
    current_process.argtypes = ()
    current_process.restype = wintypes.HANDLE
    duplicate_handle = kernel32.DuplicateHandle
    duplicate_handle.argtypes = (
        wintypes.HANDLE,
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    )
    duplicate_handle.restype = wintypes.BOOL
    compare_handles = ctypes.WinDLL("kernelbase", use_last_error=True).CompareObjectHandles
    compare_handles.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    compare_handles.restype = wintypes.BOOL
    local_process = current_process()

    for source_handle in source_handles:
        local_copy = wintypes.HANDLE()
        ctypes.set_last_error(0)
        duplicated = duplicate_handle(
            wintypes.HANDLE(target_process_handle),
            wintypes.HANDLE(source_handle),
            local_process,
            ctypes.byref(local_copy),
            0,
            False,
            _DUPLICATE_SAME_ACCESS,
        )
        if not duplicated:
            assert ctypes.get_last_error() == _ERROR_INVALID_HANDLE
            continue
        assert local_copy.value
        try:
            assert not compare_handles(
                wintypes.HANDLE(source_handle), wintypes.HANDLE(local_copy.value)
            )
        finally:
            _close_handle(int(local_copy.value))


def _open_process_for_wait(process_id: int) -> int:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_process.restype = wintypes.HANDLE
    handle = open_process(_SYNCHRONIZE, False, process_id)
    assert handle
    return int(handle)


def _wait_for_process(handle: int, timeout_ms: int) -> int:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    wait = kernel32.WaitForSingleObject
    wait.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    wait.restype = wintypes.DWORD
    return int(wait(wintypes.HANDLE(handle), timeout_ms))


def _close_handle(handle: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close = kernel32.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    assert close(wintypes.HANDLE(handle))


def _assert_paths_absent_from_fixed_launch_data(
    config: WindowsLaunchConfig,
    request: WorkerLaunchRequest,
    denied_paths: tuple[Path, ...],
) -> None:
    rendered_targets = tuple(os.fspath(path) for path in denied_paths)
    assert all(
        target.casefold() not in argument.casefold()
        for target in rendered_targets
        for argument in config.fixed_argv
    )
    bootstrap = encode_bootstrap_grant(
        BootstrapGrant(
            session_id=request.session_id,
            worker_instance_id=request.worker_instance_id,
            case_id=request.case_id,
            session_capability=request.session_capability,
        )
    )
    assert all(
        encoded not in bootstrap
        for target in rendered_targets
        for encoded in (target.encode(), target.encode("utf-16-le"))
    )


def test_live_worker_and_descendant_enforce_the_complete_isolation_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_user_temp = Path(tempfile.gettempdir())
    with tempfile.TemporaryDirectory(prefix="pws-0107-isolation-", dir=real_user_temp) as raw:
        external_root = Path(raw)
        denied_paths = _create_synthetic_targets(tmp_path / "active-case", external_root)
        original_bytes = tuple(path.read_bytes() for path in denied_paths)

        for name in _AMBIENT_NAMES:
            monkeypatch.setenv(name, _AMBIENT_CANARY)
        for name in ("LOCALAPPDATA", "TEMP", "TMP"):
            monkeypatch.setenv(name, rf"C:\{_AMBIENT_CANARY}\{name}")

        config = _launch_config()
        request = _launch_request()
        _assert_paths_absent_from_fixed_launch_data(config, request, denied_paths)

        with ExitStack() as stack:
            listeners = _LocalListeners.create(stack)
            listeners.prove_control_reachability()
            ambient_handles = tuple(_create_inheritable_event() for _index in range(4))
            for ambient_handle in ambient_handles:
                stack.callback(_close_handle, ambient_handle)
            process = WindowsProcessLauncher(config).launch(request)
            profile_path = process._profile.local_app_data
            descendant_handle = 0
            try:
                assert process.isolation.satisfies_required_boundary
                _assert_process_did_not_inherit_handles(
                    ambient_handles,
                    process._process.value,
                )
                process.transport.send_payload(
                    _encode_control(profile_path, denied_paths, listeners.ports)
                )
                received = process.transport.receive_payload(30.0)
                assert received.kind is TransportReceiveKind.PAYLOAD
                assert received.payload is not None
                (
                    mask,
                    profile_relations,
                    udp_send_reported,
                    descendant_mask,
                    descendant_profile_relations,
                    descendant_udp_send_reported,
                    descendant_pid,
                ) = _decode_response(received.payload)
                assert (
                    profile_relations & _REQUIRED_PROFILE_RELATIONS == _REQUIRED_PROFILE_RELATIONS
                )
                assert (
                    descendant_profile_relations & _REQUIRED_PROFILE_RELATIONS
                    == _REQUIRED_PROFILE_RELATIONS
                )
                # Connectionless sendto reports local acceptance under AppContainer;
                # the listener-silence assertion below is the delivery boundary. Preserve
                # the API result as a bounded bitmask instead of inferring delivery from it.
                assert 0 <= udp_send_reported <= 0b11
                assert descendant_udp_send_reported == udp_send_reported
                assert mask == _INITIAL_REQUIRED_MASK
                assert descendant_mask == _DESCENDANT_REQUIRED_MASK
                assert descendant_pid > 0
                assert all(
                    _wait_for_process(handle, 0) == _WAIT_TIMEOUT for handle in ambient_handles
                )
                for path in (*denied_paths, profile_path):
                    assert os.fspath(path).encode() not in received.payload
                    assert os.fspath(path).encode("utf-16-le") not in received.payload

                listeners.assert_no_isolated_traffic()
                assert tuple(path.read_bytes() for path in denied_paths) == original_bytes

                descendant_handle = _open_process_for_wait(descendant_pid)
                assert _wait_for_process(descendant_handle, 0) == _WAIT_TIMEOUT
                process.terminate_tree()
                assert _wait_for_process(descendant_handle, 10_000) == _WAIT_OBJECT_0
                assert process.wait(10.0) is not None
            finally:
                try:
                    process.terminate_tree()
                finally:
                    process.close()
                    if descendant_handle:
                        _close_handle(descendant_handle)
            assert not profile_path.exists()

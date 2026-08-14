"""OS-enforced AppContainer, egress, environment, and filesystem evidence."""

from __future__ import annotations

import ctypes
import json
import os
import py_compile
import socket
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path
from secrets import token_hex
from typing import Any, cast
from uuid import uuid4

import pytest

from paperworkshell.worker._windows_runtime import (
    _ALLOWED_ENVIRONMENT,
    _private_bytecode_configuration_is_valid,
)
from paperworkshell.worker.client import _windows as windows_module
from paperworkshell.worker.client._windows import (
    WindowsLaunchConfig,
    WindowsLaunchError,
    WindowsLaunchErrorCode,
    WindowsProcessLauncher,
    _AppContainerProfile,
    _approved_resource_tree,
    _minimal_environment_block,
)
from paperworkshell.worker.client.process import (
    TransportReceiveKind,
    WorkerLaunchRequest,
    WorkerResourcePolicy,
)
from paperworkshell.worker.protocol.models import SessionCapability
from tests.security.test_import_prechecks_security import (
    _create_junction,
    _remove_junction,
)
from tests.worker_resources import WorkerTestResources

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="PWS-0107 containment targets the supported Windows platform"
)

_PROBE_PORTS = (46371, 46372, 46373, 46374, 46375)


def _environment(buffer: object) -> dict[str, str]:
    rendered = cast(Any, buffer)[:]
    assert type(rendered) is str
    entries = tuple(value for value in rendered.split("\0") if value)
    return dict(entry.split("=", 1) for entry in entries)


def test_fixed_venv_startup_hint_does_not_expand_the_worker_environment_allowlist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = (tmp_path / "approved-runtime" / "Scripts" / "python.exe").resolve()
    launcher.parent.mkdir(parents=True)
    launcher.write_bytes(b"synthetic fixed launcher")
    monkeypatch.setenv("PATH", "ambient-path-canary")
    monkeypatch.setenv("PYTHONPATH", "ambient-pythonpath-canary")

    environment = _environment(
        _minimal_environment_block(
            tmp_path / "app-container-profile",
            venv_launcher=launcher,
        )
    )

    assert environment["__PYVENV_LAUNCHER__"] == os.fspath(launcher)
    assert "PATH" not in environment
    assert "PYTHONPATH" not in environment
    assert "__PYVENV_LAUNCHER__" not in _ALLOWED_ENVIRONMENT


def test_hardlinked_runtime_file_is_rejected_before_access_grant(tmp_path: Path) -> None:
    runtime_root = tmp_path / "approved-runtime"
    outside_root = tmp_path / "shared-cache"
    runtime_root.mkdir()
    outside_root.mkdir()
    shared = outside_root / "native-package.pyd"
    alias = runtime_root / "native-package.pyd"
    shared.write_bytes(b"synthetic native package")
    os.link(shared, alias)

    with pytest.raises(WindowsLaunchError) as caught:
        _approved_resource_tree(runtime_root)

    assert caught.value.code is WindowsLaunchErrorCode.CONFIGURATION_INVALID
    assert caught.value.context == "config.resource_links"
    rendered = str(caught.value)
    assert os.fspath(shared) not in rendered
    assert os.fspath(alias) not in rendered


def test_mutable_python_bytecode_caches_are_not_worker_access_authorities(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "approved-runtime"
    package = runtime_root / "package"
    cache = package / "__PyCaChE__"
    cache.mkdir(parents=True)
    source = package / "module.py"
    cached = cache / "module.cpython-314.pyc"
    standalone_cache = package / "optimized.PyC"
    legacy_cache = package / "legacy.PYO"
    source.write_text("VALUE = 'approved'\n", encoding="utf-8")
    cached.write_bytes(b"mutable bytecode cache")
    standalone_cache.write_bytes(b"mutable optimized bytecode cache")
    legacy_cache.write_bytes(b"mutable legacy bytecode cache")

    first = frozenset(_approved_resource_tree(runtime_root))
    cached.write_bytes(b"replaced bytecode cache")
    second = frozenset(_approved_resource_tree(runtime_root))

    assert first == second
    assert runtime_root in first
    assert package in first
    assert source in first
    assert cache not in first
    assert cached not in first
    assert standalone_cache not in first
    assert legacy_cache not in first
    for mutable_cache_root in (cache, cached, standalone_cache, legacy_cache):
        with pytest.raises(WindowsLaunchError) as cache_root_error:
            _approved_resource_tree(mutable_cache_root)
        assert cache_root_error.value.code is WindowsLaunchErrorCode.CONFIGURATION_INVALID
        assert cache_root_error.value.context == "config.resources"


def test_private_pycache_prefix_ignores_a_valid_conflicting_source_cache(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "approved-source"
    source_root.mkdir()
    module = source_root / "cache_canary.py"
    runner = source_root / "runner.py"
    module.write_text("VALUE = 'cached'\n", encoding="utf-8")
    source_stat = module.stat()
    compiled = py_compile.compile(
        os.fspath(module),
        doraise=True,
        invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP,
    )
    assert type(compiled) is str
    cached = Path(compiled)
    cached_bytes = cached.read_bytes()
    module.write_text("VALUE = 'source'\n", encoding="utf-8")
    os.utime(module, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))
    runner.write_text(
        "import sys\n"
        f"sys.path.insert(0, {os.fspath(source_root)!r})\n"
        "import cache_canary\n"
        "sys.stdout.write(cache_canary.VALUE)\n",
        encoding="utf-8",
    )
    base_arguments = (
        sys.executable,
        "-I",
        "-S",
        "-B",
        "-X",
        "utf8",
    )
    cached_run = subprocess.run(
        (*base_arguments, os.fspath(runner)),
        check=False,
        capture_output=True,
        timeout=10.0,
    )
    assert cached_run.returncode == 0
    assert cached_run.stdout == b"cached"

    profile_root = tmp_path / "profile"
    pycache_prefix = profile_root / "Temp" / "PyCache"
    pycache_prefix.mkdir(parents=True)
    isolated_run = subprocess.run(
        (
            *base_arguments,
            "-X",
            f"pycache_prefix={pycache_prefix}",
            os.fspath(runner),
        ),
        check=False,
        capture_output=True,
        timeout=10.0,
    )

    assert isolated_run.returncode == 0
    assert isolated_run.stdout == b"source"
    assert isolated_run.stderr == b""
    assert cached.read_bytes() == cached_bytes
    assert not tuple(pycache_prefix.iterdir())


def test_private_pycache_configuration_rejects_an_empty_wrong_parent(
    tmp_path: Path,
) -> None:
    local_app_data = tmp_path / "profile"
    expected = local_app_data / "Temp" / "PyCache"
    unrelated = tmp_path / "unrelated" / "PyCache"
    expected.mkdir(parents=True)
    unrelated.mkdir(parents=True)
    nested_local_app_data = local_app_data / "Local"
    nested_local_app_data.mkdir()

    assert _private_bytecode_configuration_is_valid(
        os.fspath(local_app_data),
        os.fspath(local_app_data),
        os.fspath(local_app_data / "Temp"),
        os.fspath(local_app_data / "Temp"),
        os.fspath(expected),
        os.fspath(expected),
    )
    assert _private_bytecode_configuration_is_valid(
        os.fspath(local_app_data),
        os.fspath(nested_local_app_data),
        os.fspath(nested_local_app_data / "Temp"),
        os.fspath(nested_local_app_data / "Temp"),
        os.fspath(expected),
        os.fspath(nested_local_app_data / "Temp" / "PyCache"),
    )
    assert not _private_bytecode_configuration_is_valid(
        os.fspath(local_app_data),
        os.fspath(local_app_data),
        os.fspath(local_app_data / "Temp"),
        os.fspath(local_app_data / "Temp"),
        os.fspath(unrelated),
        os.fspath(unrelated),
    )
    assert not _private_bytecode_configuration_is_valid(
        os.fspath(local_app_data),
        os.fspath(local_app_data),
        os.fspath(local_app_data / "Temp"),
        os.fspath(local_app_data / "Temp"),
        os.fspath(expected),
        os.fspath(unrelated),
    )


@pytest.mark.parametrize(
    ("profile", "local", "temp", "tmp", "configured", "active"),
    (
        (
            "relative-profile",
            "profile",
            "profile\\Temp",
            "profile\\Temp",
            "profile\\Temp\\PyCache",
            "profile\\Temp\\PyCache",
        ),
        (
            "profile",
            "relative-local",
            "profile\\Temp",
            "profile\\Temp",
            "profile\\Temp\\PyCache",
            "profile\\Temp\\PyCache",
        ),
        (
            "profile",
            "profile",
            "profile\\nested\\Temp",
            "profile\\nested\\Temp",
            "profile\\Temp\\PyCache",
            "profile\\nested\\Temp\\PyCache",
        ),
        (
            "profile",
            "profile",
            "profile\\Temp",
            "profile\\OtherTemp",
            "profile\\Temp\\PyCache",
            "profile\\Temp\\PyCache",
        ),
        (
            "profile",
            "profile",
            "profile\\Temp",
            "profile\\Temp",
            "profile\\OtherCache",
            "profile\\Temp\\PyCache",
        ),
        (
            "profile",
            "profile",
            "profile\\Temp",
            "profile\\Temp",
            "profile\\Temp\\PyCache",
            "profile\\OtherCache",
        ),
        (
            "profile",
            "profile\x00alias",
            "profile\\Temp",
            "profile\\Temp",
            "profile\\Temp\\PyCache",
            "profile\\Temp\\PyCache",
        ),
        (
            "profile",
            "profile",
            "profile\\Temp\x00alias",
            "profile\\Temp",
            "profile\\Temp\\PyCache",
            "profile\\Temp\\PyCache",
        ),
        (
            "profile",
            "profile",
            "profile\\Temp",
            "profile\\Temp\x00alias",
            "profile\\Temp\\PyCache",
            "profile\\Temp\\PyCache",
        ),
    ),
)
def test_private_pycache_configuration_rejects_unbound_environment_values(
    tmp_path: Path,
    profile: str,
    local: str,
    temp: str,
    tmp: str,
    configured: str,
    active: str,
) -> None:
    def resolve(value: str) -> str:
        if value.startswith("relative-"):
            return value
        return os.fspath(tmp_path / value)

    assert not _private_bytecode_configuration_is_valid(
        resolve(profile),
        resolve(local),
        resolve(temp),
        resolve(tmp),
        resolve(configured),
        resolve(active),
    )


def test_create_hardlink_after_inventory_is_blocked_for_full_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "approved-runtime"
    outside_root = tmp_path / "outside-runtime"
    runtime_root.mkdir()
    outside_root.mkdir()
    approved = runtime_root / "module.py"
    outside = outside_root / "outside.py"
    staged = runtime_root / "staged-hardlink.py"
    late_created = runtime_root / "late-created.py"
    outward_alias = outside_root / "approved-alias.py"
    approved.write_text("VALUE = 'approved'\n", encoding="utf-8")
    outside.write_text("VALUE = 'outside'\n", encoding="utf-8")
    profile = _AppContainerProfile.create()
    profile_folder = profile.local_app_data
    original_change = windows_module._change_sid_access_handle
    swap_error: OSError | None = None
    attempted = False

    assert windows_module._sid_access_mask(outside, profile.sid) == 0

    def stage_and_swap(handle: int, sid: ctypes.c_void_p, mode: int) -> None:
        nonlocal attempted, swap_error
        if mode == windows_module._GRANT_ACCESS and not attempted:
            attempted = True
            late_created.write_text("VALUE = 'late'\n", encoding="utf-8")
            try:
                os.link(outside, staged)
                os.replace(staged, approved)
            except OSError as error:
                swap_error = error
        original_change(handle, sid, mode)

    monkeypatch.setattr(
        windows_module,
        "_change_sid_access_handle",
        stage_and_swap,
    )
    try:
        profile.grant_read_execute((runtime_root,))
        profile.require_read_execute((runtime_root, approved))
        assert attempted
        assert swap_error is not None
        assert approved.read_text(encoding="utf-8") == "VALUE = 'approved'\n"
        if staged.exists():
            assert staged.samefile(outside)
        with pytest.raises(PermissionError):
            os.link(approved, outward_alias)
        assert windows_module._sid_access_mask(outside, profile.sid) == 0
        assert windows_module._sid_access_mask(late_created, profile.sid) == 0
    finally:
        assert profile.close_no_raise()
        staged.unlink(missing_ok=True)
        late_created.unlink(missing_ok=True)

    os.link(approved, outward_alias)
    assert outward_alias.samefile(approved)
    outward_alias.unlink()
    assert not profile_folder.exists()


def test_after_scan_ancestor_junction_swap_is_blocked_before_acl_grant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ancestor = tmp_path / "approved-parent"
    runtime_root = ancestor / "runtime"
    outside = tmp_path / "outside-runtime"
    displaced = tmp_path / "displaced-approved-parent"
    runtime_root.mkdir(parents=True)
    outside.mkdir()
    (runtime_root / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (outside / "outside.py").write_text("VALUE = 2\n", encoding="utf-8")
    profile = _AppContainerProfile.create()
    profile_folder = profile.local_app_data
    original_change = windows_module._change_sid_access_handle
    pivot_error: OSError | None = None
    attempted = False

    assert windows_module._sid_access_mask(outside, profile.sid) == 0

    def pivot_ancestor(handle: int, sid: ctypes.c_void_p, mode: int) -> None:
        nonlocal attempted, pivot_error
        if mode == windows_module._GRANT_ACCESS and not attempted:
            attempted = True
            try:
                ancestor.rename(displaced)
            except OSError as error:
                pivot_error = error
            else:
                _create_junction(ancestor, outside)
        original_change(handle, sid, mode)

    monkeypatch.setattr(
        windows_module,
        "_change_sid_access_handle",
        pivot_ancestor,
    )
    try:
        profile.grant_read_execute((runtime_root,))
        assert attempted
        assert pivot_error is not None
        assert not os.path.isjunction(ancestor)
        assert windows_module._sid_access_mask(outside, profile.sid) == 0
    finally:
        assert profile.close_no_raise()
        if os.path.isjunction(ancestor):
            _remove_junction(ancestor)
        if displaced.exists() and not ancestor.exists():
            displaced.rename(ancestor)

    assert not profile_folder.exists()


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


def test_live_app_container_reads_guarded_runtime_while_user_hardlinks_are_denied(
    worker_test_resources: WorkerTestResources,
) -> None:
    source = worker_test_resources.source_root / "paperworkshell" / "worker" / "_windows_runtime.py"
    alias = worker_test_resources.stage_root / "guarded-runtime-alias.py"
    process = WindowsProcessLauncher(_launch_config(worker_test_resources)).launch(
        _launch_request()
    )
    profile_folder = process._profile.local_app_data
    try:
        received = process.transport.receive_payload(15.0)
        assert received.kind is TransportReceiveKind.PAYLOAD
        assert received.payload is not None
        result: dict[str, Any] = json.loads(received.payload)
        assert result["approved_source_read"] is True
        with pytest.raises(PermissionError):
            os.link(source, alias)
        assert process.wait(15.0) == 0
    finally:
        process.close()

    os.link(source, alias)
    assert alias.samefile(source)
    alias.unlink()
    assert not profile_folder.exists()


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

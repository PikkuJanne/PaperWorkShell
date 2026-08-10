"""Fixed AppContainer probe; never selected through a production RPC operation."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

_SOURCE_ROOT = Path(__file__).parents[2] / "src"
sys.path.insert(0, str(_SOURCE_ROOT))

from paperworkshell.worker._windows_runtime import (  # noqa: E402
    read_bootstrap_from_standard_error,
    verify_runtime_isolation,
)
from paperworkshell.worker.protocol.bootstrap import decode_bootstrap_grant  # noqa: E402
from paperworkshell.worker.protocol.framing import write_frame  # noqa: E402

_PROBE_PORTS = (46371, 46372, 46373, 46374, 46375)
_ALLOWED_ENVIRONMENT = {
    "SYSTEMROOT",
    "WINDIR",
    "LOCALAPPDATA",
    "PWS_PROFILE_ROOT",
    "TEMP",
    "TMP",
    "PYTHONIOENCODING",
    "PYTHONUTF8",
}


def _tcp_connected(family: socket.AddressFamily, host: str) -> bool:
    for port in _PROBE_PORTS:
        try:
            with socket.socket(family, socket.SOCK_STREAM) as client:
                client.settimeout(0.2)
                if client.connect_ex((host, port)) == 0:
                    return True
        except OSError:
            continue
    return False


def _send_udp(family: socket.AddressFamily, host: str) -> bool:
    reported_success = False
    for port in _PROBE_PORTS:
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as client:
                reported_success = client.sendto(b"pws-egress-probe", (host, port)) > 0
        except OSError:
            continue
    return reported_success


def _can_read(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            return bool(stream.read(1))
    except OSError:
        return False


def _can_create(path: Path) -> bool:
    try:
        with path.open("xb") as stream:
            stream.write(b"probe")
    except OSError:
        return False
    try:
        path.unlink()
    except OSError:
        pass
    return True


def _os_error_stage(error: OSError) -> int:
    for candidate in (getattr(error, "winerror", None), error.errno):
        if type(candidate) is int and 0 < candidate <= 65_535:
            return 100_000 + candidate
    return 100_000


def _nonzero_exit_stage(returncode: int, stderr: bytes) -> int:
    status = returncode & 0xFFFF_FFFF
    if status == 0xC000_0135:
        return 31
    if status == 0xC000_0142:
        return 32
    if status == 0xC000_0005:
        return 33
    lowered = stderr.lower()
    if b"failed to get the python codec" in lowered or b"no module named 'encodings'" in lowered:
        return 37
    if b"no module named" in lowered:
        return 34
    if b"permissionerror" in lowered or b"access is denied" in lowered:
        return 35
    if b"can't open file" in lowered:
        return 36
    return 39


def _descendant_result() -> dict[str, bool | int]:
    child = Path(__file__).with_name("worker_containment_descendant_probe.py")
    pycache_prefix = sys.pycache_prefix
    if type(pycache_prefix) is not str or not pycache_prefix:
        return {"completed": False, "failure_stage": 38}
    try:
        completed = subprocess.run(
            (
                sys.executable,
                "-I",
                "-S",
                "-B",
                "-X",
                "utf8",
                "-X",
                f"pycache_prefix={pycache_prefix}",
                os.fspath(child),
            ),
            check=False,
            input=b"",
            capture_output=True,
            timeout=5.0,
            shell=False,
            env=dict(os.environ),
        )
        if completed.returncode != 0:
            return {
                "completed": False,
                "failure_stage": _nonzero_exit_stage(completed.returncode, completed.stderr),
            }
        try:
            decoded = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return {"completed": False, "failure_stage": 41}
        if type(decoded) is not dict or any(type(value) is not bool for value in decoded.values()):
            return {"completed": False, "failure_stage": 42}
        return {"completed": True, "failure_stage": 0, **decoded}
    except OSError as error:
        return {"completed": False, "failure_stage": _os_error_stage(error)}
    except subprocess.TimeoutExpired:
        return {"completed": False, "failure_stage": 21}
    except subprocess.SubprocessError:
        return {"completed": False, "failure_stage": 29}


def main() -> int:
    grant = decode_bootstrap_grant(read_bootstrap_from_standard_error())
    del grant
    evidence = verify_runtime_isolation()
    helper_directory = Path(__file__).parent
    repository_root = Path(__file__).parents[2]
    result = {
        "app_container": evidence.app_container,
        "zero_capabilities": evidence.zero_capabilities,
        "in_job": evidence.in_job,
        "minimal_environment": evidence.minimal_environment,
        "unexpected_environment": any(
            name.upper() not in _ALLOWED_ENVIRONMENT for name in os.environ
        ),
        "tcp4_connected": _tcp_connected(socket.AF_INET, "127.0.0.1"),
        "tcp6_connected": _tcp_connected(socket.AF_INET6, "::1"),
        "udp4_send_reported": _send_udp(socket.AF_INET, "127.0.0.1"),
        "udp6_send_reported": _send_udp(socket.AF_INET6, "::1"),
        "approved_source_read": _can_read(Path(__file__)),
        "approved_source_write": _can_create(helper_directory / ".pws-approved-write-probe"),
        "outside_source_read": _can_read(repository_root / "pyproject.toml"),
        "outside_source_write": _can_create(repository_root / ".pws-outside-write-probe"),
        "descendant": _descendant_result(),
    }
    writer = getattr(sys.stdout.buffer, "raw", sys.stdout.buffer)
    write_frame(
        writer,
        json.dumps(result, sort_keys=True, separators=(",", ":")).encode("ascii"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

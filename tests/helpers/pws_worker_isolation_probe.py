"""Fixed live-isolation probe used only by the PWS-0107 security tests.

The initial process is launched through ``WindowsProcessLauncher`` exactly like
the production worker.  Dynamic synthetic targets arrive only afterward on the
already-contained framed test channel; they are never added to argv, the
environment, or the one-shot bootstrap grant.  Every response is a fixed set of
numeric evidence fields so paths, environment values, and file content cannot
flow back across the boundary.
"""

from __future__ import annotations

import ctypes
import json
import os
import socket
import subprocess
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Final

_SOURCE_ROOT = Path(__file__).parents[2] / "src"
sys.path.insert(0, os.fspath(_SOURCE_ROOT))

from paperworkshell.worker._windows_runtime import (  # noqa: E402
    read_bootstrap_from_standard_error,
    verify_runtime_isolation,
)
from paperworkshell.worker.protocol.bootstrap import decode_bootstrap_grant  # noqa: E402
from paperworkshell.worker.protocol.framing import read_frame, write_frame  # noqa: E402

_DESCENDANT_ARGUMENT: Final = "--pws-isolation-descendant"
_STD_INPUT_HANDLE: Final = -10
_STD_OUTPUT_HANDLE: Final = -11
_STD_ERROR_HANDLE: Final = -12
_STANDARD_HANDLE_IDS: Final = (_STD_INPUT_HANDLE, _STD_OUTPUT_HANDLE, _STD_ERROR_HANDLE)
_INVALID_HANDLE_VALUE: Final = ctypes.c_void_p(-1).value
_DUPLICATE_SAME_ACCESS: Final = 0x0000_0002
_ERROR_INVALID_HANDLE: Final = 6
_AMBIENT_CANARY: Final = "PWS_AMBIENT_ENV_CANARY_0107"
_AMBIENT_NAMES: Final = frozenset(
    {
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
    }
)

APP_CONTAINER: Final = 1 << 0
ZERO_CAPABILITIES: Final = 1 << 1
IN_JOB: Final = 1 << 2
MINIMAL_ENVIRONMENT: Final = 1 << 3
AMBIENT_ENVIRONMENT_ABSENT: Final = 1 << 4
PROFILE_LOCAL_APP_DATA_IS_SCOPED: Final = 1 << 5
PROFILE_TEMP_IS_SCOPED: Final = 1 << 6
PROFILE_TEMP_WRITE_SUCCEEDED: Final = 1 << 7
TCP4_BLOCKED: Final = 1 << 8
UDP4_PROBE_ATTEMPTED: Final = 1 << 9
TCP6_BLOCKED: Final = 1 << 10
UDP6_PROBE_ATTEMPTED: Final = 1 << 11
APPROVED_CODE_READ_SUCCEEDED: Final = 1 << 12
DENIED_READS_BLOCKED: Final = 1 << 13
DENIED_WRITES_BLOCKED: Final = 1 << 14
TARGETS_ABSENT_FROM_ARGV_ENVIRONMENT: Final = 1 << 15
TARGETS_ABSENT_FROM_BOOTSTRAP: Final = 1 << 16
STANDARD_HANDLES_NON_INHERITABLE: Final = 1 << 18
PARENT_STANDARD_HANDLES_ABSENT: Final = 1 << 19

_BASE_REQUIRED_MASK: Final = (
    APP_CONTAINER
    | ZERO_CAPABILITIES
    | IN_JOB
    | MINIMAL_ENVIRONMENT
    | AMBIENT_ENVIRONMENT_ABSENT
    | PROFILE_LOCAL_APP_DATA_IS_SCOPED
    | PROFILE_TEMP_IS_SCOPED
    | PROFILE_TEMP_WRITE_SUCCEEDED
    | TCP4_BLOCKED
    | UDP4_PROBE_ATTEMPTED
    | TCP6_BLOCKED
    | UDP6_PROBE_ATTEMPTED
    | APPROVED_CODE_READ_SUCCEEDED
    | DENIED_READS_BLOCKED
    | DENIED_WRITES_BLOCKED
    | TARGETS_ABSENT_FROM_ARGV_ENVIRONMENT
    | STANDARD_HANDLES_NON_INHERITABLE
)
INITIAL_REQUIRED_MASK: Final = _BASE_REQUIRED_MASK | TARGETS_ABSENT_FROM_BOOTSTRAP
DESCENDANT_REQUIRED_MASK: Final = _BASE_REQUIRED_MASK | PARENT_STANDARD_HANDLES_ABSENT

_CONTROL_KEYS: Final = frozenset(
    {
        "profile_path",
        "denied_read_paths",
        "denied_write_paths",
        "ports",
    }
)
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
_DESCENDANT_RESPONSE_KEYS: Final = frozenset({"mask", "profile_relations", "udp_send_reported"})
_PORT_COUNT: Final = 4
_MAXIMUM_TARGETS: Final = 32


@dataclass(frozen=True, slots=True)
class _Control:
    profile_path: Path
    denied_read_paths: tuple[Path, ...]
    denied_write_paths: tuple[Path, ...]
    ports: tuple[int, int, int, int]

    @property
    def forbidden_paths(self) -> tuple[Path, ...]:
        return tuple(dict.fromkeys((*self.denied_read_paths, *self.denied_write_paths)))


def _decode_control(payload: bytes) -> _Control:
    try:
        decoded: Any = json.loads(payload)
    except UnicodeDecodeError, json.JSONDecodeError:
        raise ValueError("invalid control") from None
    if type(decoded) is not dict or frozenset(decoded) != _CONTROL_KEYS:
        raise ValueError("invalid control")
    profile = decoded["profile_path"]
    read_paths = decoded["denied_read_paths"]
    write_paths = decoded["denied_write_paths"]
    ports = decoded["ports"]
    if (
        type(profile) is not str
        or "\x00" in profile
        or not Path(profile).is_absolute()
        or type(read_paths) is not list
        or type(write_paths) is not list
        or not 1 <= len(read_paths) <= _MAXIMUM_TARGETS
        or not 1 <= len(write_paths) <= _MAXIMUM_TARGETS
        or any(
            type(value) is not str or "\x00" in value or not Path(value).is_absolute()
            for value in (*read_paths, *write_paths)
        )
        or type(ports) is not list
        or len(ports) != _PORT_COUNT
        or any(type(port) is not int or not 1 <= port <= 65_535 for port in ports)
    ):
        raise ValueError("invalid control")
    return _Control(
        profile_path=Path(profile),
        denied_read_paths=tuple(Path(value) for value in read_paths),
        denied_write_paths=tuple(Path(value) for value in write_paths),
        ports=(ports[0], ports[1], ports[2], ports[3]),
    )


def _read_control(reader: BinaryIO) -> tuple[bytes, _Control]:
    payload = read_frame(reader)
    if payload is None:
        raise ValueError("missing control")
    return payload, _decode_control(payload)


def _normalized(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def _is_within(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((_normalized(path), _normalized(root))) == _normalized(root)
    except ValueError:
        return False


def _can_read(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            return bool(stream.read(1))
    except OSError:
        return False


def _can_open_for_write(path: Path) -> bool:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND)
    except OSError:
        return False
    os.close(descriptor)
    return True


def _can_write_profile_temp(profile_temp: Path, suffix: str) -> bool:
    candidate = profile_temp / f"pws-isolation-{suffix}.tmp"
    try:
        profile_temp.mkdir(parents=True, exist_ok=True)
        with candidate.open("xb") as stream:
            stream.write(b"pws-isolation-probe")
        candidate.unlink()
    except OSError:
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


def _tcp_blocked(family: socket.AddressFamily, host: str, port: int) -> bool:
    try:
        with socket.socket(family, socket.SOCK_STREAM) as client:
            client.settimeout(1.0)
            return client.connect_ex((host, port)) != 0
    except OSError:
        return True


def _udp_send_reported(family: socket.AddressFamily, host: str, port: int) -> bool:
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as client:
            client.settimeout(1.0)
            return client.sendto(b"pws-isolation-probe", (host, port)) > 0
    except OSError:
        return False


def _environment_is_clean(startup_environment: tuple[tuple[str, str], ...]) -> bool:
    upper_names = {name.upper() for name, _value in startup_environment}
    values = tuple(value for _name, value in startup_environment)
    return _AMBIENT_NAMES.isdisjoint(upper_names) and all(
        _AMBIENT_CANARY not in value for value in values
    )


def _standard_handle_values() -> tuple[int, int, int]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_std_handle = kernel32.GetStdHandle
    get_std_handle.argtypes = (wintypes.DWORD,)
    get_std_handle.restype = wintypes.HANDLE
    values: list[int] = []
    for identifier in _STANDARD_HANDLE_IDS:
        handle = get_std_handle(wintypes.DWORD(identifier & 0xFFFF_FFFF))
        if not handle or handle == _INVALID_HANDLE_VALUE:
            raise ValueError("standard handle unavailable")
        values.append(int(handle))
    return values[0], values[1], values[2]


def _close_raw_handle(handle: int) -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    return bool(close_handle(wintypes.HANDLE(handle)))


def _process_handles_are_distinct_from_sources(
    source_handles: tuple[int, ...],
    target_process_handle: int,
) -> bool:
    """Compare objects across processes; HANDLE integers alone are not identities."""

    if not source_handles or target_process_handle <= 0:
        return False
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
    try:
        for source_handle in source_handles:
            local_copy = wintypes.HANDLE()
            ctypes.set_last_error(0)
            if not duplicate_handle(
                wintypes.HANDLE(target_process_handle),
                wintypes.HANDLE(source_handle),
                local_process,
                ctypes.byref(local_copy),
                0,
                False,
                _DUPLICATE_SAME_ACCESS,
            ):
                if ctypes.get_last_error() != _ERROR_INVALID_HANDLE:
                    return False
                continue
            if not local_copy.value:
                return False
            try:
                same_object = bool(
                    compare_handles(
                        wintypes.HANDLE(source_handle), wintypes.HANDLE(local_copy.value)
                    )
                )
            finally:
                copy_closed = _close_raw_handle(int(local_copy.value))
            if same_object or not copy_closed:
                return False
    except OSError, TypeError, ValueError, AttributeError:
        return False
    return True


def _encode_descendant_control(control: _Control) -> bytes:
    return json.dumps(
        {
            "profile_path": os.fspath(control.profile_path),
            "denied_read_paths": [os.fspath(path) for path in control.denied_read_paths],
            "denied_write_paths": [os.fspath(path) for path in control.denied_write_paths],
            "ports": list(control.ports),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _profile_relations(control: _Control) -> int:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
    temp = Path(os.environ.get("TEMP", ""))
    temporary = Path(os.environ.get("TMP", ""))
    relations = 0
    if _normalized(local_app_data) == _normalized(control.profile_path):
        relations |= 1 << 0
    if _is_within(local_app_data, control.profile_path):
        relations |= 1 << 1
    if _is_within(control.profile_path, local_app_data):
        relations |= 1 << 2
    if _normalized(temp) == _normalized(temporary):
        relations |= 1 << 3
    if _is_within(temp, control.profile_path):
        relations |= 1 << 4
    if _is_within(control.profile_path, temp):
        relations |= 1 << 5
    if _is_within(temp, local_app_data):
        relations |= 1 << 6
    if _is_within(local_app_data, temp):
        relations |= 1 << 7
    return relations


def _targets_absent_from_startup(
    control: _Control,
    startup_arguments: tuple[str, ...],
    startup_environment: tuple[tuple[str, str], ...],
) -> bool:
    startup_values = (*startup_arguments, *(value for _name, value in startup_environment))
    normalized_values = tuple(value.casefold() for value in startup_values)
    return all(
        all(os.fspath(path).casefold() not in value for value in normalized_values)
        for path in control.forbidden_paths
    )


def _targets_absent_from_bootstrap(control: _Control, bootstrap: bytes) -> bool:
    encodings = tuple(
        encoded
        for path in control.forbidden_paths
        for encoded in (os.fspath(path).encode(), os.fspath(path).encode("utf-16-le"))
    )
    return all(encoded not in bootstrap for encoded in encodings)


def _probe_mask(
    control: _Control,
    *,
    suffix: str,
    startup_arguments: tuple[str, ...],
    startup_environment: tuple[tuple[str, str], ...],
    bootstrap: bytes | None,
) -> tuple[int, int, int]:
    evidence = verify_runtime_isolation()
    mask = 0
    if evidence.app_container:
        mask |= APP_CONTAINER
    if evidence.zero_capabilities:
        mask |= ZERO_CAPABILITIES
    if evidence.in_job:
        mask |= IN_JOB
    if evidence.minimal_environment:
        mask |= MINIMAL_ENVIRONMENT
    if evidence.standard_handles_non_inheritable:
        mask |= STANDARD_HANDLES_NON_INHERITABLE
    if _environment_is_clean(startup_environment):
        mask |= AMBIENT_ENVIRONMENT_ABSENT

    local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
    temp = Path(os.environ.get("TEMP", ""))
    temporary = Path(os.environ.get("TMP", ""))
    if _is_within(local_app_data, control.profile_path):
        mask |= PROFILE_LOCAL_APP_DATA_IS_SCOPED
    if (
        _normalized(temp) == _normalized(temporary)
        and _is_within(temp, control.profile_path)
        and _is_within(temp, local_app_data)
    ):
        mask |= PROFILE_TEMP_IS_SCOPED
    if _can_write_profile_temp(temp, suffix):
        mask |= PROFILE_TEMP_WRITE_SUCCEEDED

    tcp4, udp4, tcp6, udp6 = control.ports
    udp_send_reported = 0
    if _tcp_blocked(socket.AF_INET, "127.0.0.1", tcp4):
        mask |= TCP4_BLOCKED
    if _udp_send_reported(socket.AF_INET, "127.0.0.1", udp4):
        udp_send_reported |= 1 << 0
    mask |= UDP4_PROBE_ATTEMPTED
    if _tcp_blocked(socket.AF_INET6, "::1", tcp6):
        mask |= TCP6_BLOCKED
    if _udp_send_reported(socket.AF_INET6, "::1", udp6):
        udp_send_reported |= 1 << 1
    mask |= UDP6_PROBE_ATTEMPTED

    if _can_read(Path(__file__)):
        mask |= APPROVED_CODE_READ_SUCCEEDED
    if all(not _can_read(path) for path in control.denied_read_paths):
        mask |= DENIED_READS_BLOCKED
    if all(not _can_open_for_write(path) for path in control.denied_write_paths):
        mask |= DENIED_WRITES_BLOCKED
    if _targets_absent_from_startup(control, startup_arguments, startup_environment):
        mask |= TARGETS_ABSENT_FROM_ARGV_ENVIRONMENT
    if bootstrap is not None and _targets_absent_from_bootstrap(control, bootstrap):
        mask |= TARGETS_ABSENT_FROM_BOOTSTRAP
    try:
        return mask, _profile_relations(control), udp_send_reported
    except Exception:
        return -8, 0, udp_send_reported


def _fixed_response_payload(values: dict[str, int], expected_keys: frozenset[str]) -> bytes:
    if frozenset(values) != expected_keys or any(
        type(value) is not int for value in values.values()
    ):
        raise ValueError("invalid evidence")
    return json.dumps(values, sort_keys=True, separators=(",", ":")).encode("ascii")


def _decode_descendant_response(payload: bytes) -> tuple[int, int, int]:
    try:
        decoded: Any = json.loads(payload)
    except UnicodeDecodeError, json.JSONDecodeError:
        raise ValueError("invalid descendant evidence") from None
    if (
        type(decoded) is not dict
        or frozenset(decoded) != _DESCENDANT_RESPONSE_KEYS
        or type(decoded["mask"]) is not int
        or type(decoded["profile_relations"]) is not int
        or type(decoded["udp_send_reported"]) is not int
    ):
        raise ValueError("invalid descendant evidence")
    return decoded["mask"], decoded["profile_relations"], decoded["udp_send_reported"]


def _spawn_descendant(
    control: _Control,
    parent_standard_handles: tuple[int, int, int],
) -> tuple[subprocess.Popen[bytes], int, int, int]:
    arguments = (
        sys.executable,
        "-I",
        "-S",
        "-B",
        "-X",
        "utf8",
        os.fspath(Path(__file__)),
        _DESCENDANT_ARGUMENT,
    )
    child = subprocess.Popen(
        arguments,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        close_fds=False,
        shell=False,
        env=dict(os.environ),
    )
    child_process_handle = getattr(child, "_handle", 0)
    endpoints_absent = (
        isinstance(child_process_handle, int)
        and not isinstance(child_process_handle, bool)
        and (
            _process_handles_are_distinct_from_sources(
                parent_standard_handles,
                int(child_process_handle),
            )
        )
    )
    if child.stdin is None or child.stdout is None:
        child.kill()
        raise ValueError("descendant channel unavailable")
    write_frame(child.stdin, _encode_descendant_control(control))
    child.stdin.flush()
    child.stdin.close()
    response = read_frame(child.stdout)
    child.stdout.close()
    if response is None:
        child.kill()
        raise ValueError("descendant evidence unavailable")
    descendant_mask, profile_relations, udp_send_reported = _decode_descendant_response(response)
    if endpoints_absent:
        descendant_mask |= PARENT_STANDARD_HANDLES_ABSENT
    return child, descendant_mask, profile_relations, udp_send_reported


def _wait_for_job_termination() -> None:
    while True:
        time.sleep(60.0)


def _descendant_main() -> int:
    startup_arguments = tuple(sys.argv)
    startup_environment = tuple(os.environ.items())
    writer = getattr(sys.stdout.buffer, "raw", sys.stdout.buffer)
    try:
        control_payload, control = _read_control(sys.stdin.buffer)
        del control_payload
    except Exception:
        write_frame(
            writer,
            _fixed_response_payload(
                {"mask": -1, "profile_relations": 0, "udp_send_reported": 0},
                _DESCENDANT_RESPONSE_KEYS,
            ),
        )
        return 3
    try:
        mask, profile_relations, udp_send_reported = _probe_mask(
            control,
            suffix="descendant",
            startup_arguments=startup_arguments,
            startup_environment=startup_environment,
            bootstrap=None,
        )
    except Exception:
        write_frame(
            writer,
            _fixed_response_payload(
                {"mask": -2, "profile_relations": 0, "udp_send_reported": 0},
                _DESCENDANT_RESPONSE_KEYS,
            ),
        )
        return 3
    write_frame(
        writer,
        _fixed_response_payload(
            {
                "mask": mask,
                "profile_relations": profile_relations,
                "udp_send_reported": udp_send_reported,
            },
            _DESCENDANT_RESPONSE_KEYS,
        ),
    )
    _wait_for_job_termination()
    return 0


def _initial_main() -> int:
    startup_arguments = tuple(sys.argv)
    startup_environment = tuple(os.environ.items())
    writer = getattr(sys.stdout.buffer, "raw", sys.stdout.buffer)
    try:
        bootstrap = read_bootstrap_from_standard_error()
        decode_bootstrap_grant(bootstrap)
    except Exception:
        write_frame(
            writer,
            _fixed_response_payload(
                {
                    "mask": -1,
                    "profile_relations": 0,
                    "udp_send_reported": 0,
                    "descendant_mask": 0,
                    "descendant_profile_relations": 0,
                    "descendant_udp_send_reported": 0,
                    "descendant_pid": 0,
                },
                _RESPONSE_KEYS,
            ),
        )
        return 3
    reader = getattr(sys.stdin.buffer, "raw", sys.stdin.buffer)
    try:
        control_payload, control = _read_control(reader)
        del control_payload
    except Exception:
        write_frame(
            writer,
            _fixed_response_payload(
                {
                    "mask": -2,
                    "profile_relations": 0,
                    "udp_send_reported": 0,
                    "descendant_mask": 0,
                    "descendant_profile_relations": 0,
                    "descendant_udp_send_reported": 0,
                    "descendant_pid": 0,
                },
                _RESPONSE_KEYS,
            ),
        )
        return 3
    try:
        mask, profile_relations, udp_send_reported = _probe_mask(
            control,
            suffix="initial",
            startup_arguments=startup_arguments,
            startup_environment=startup_environment,
            bootstrap=bootstrap,
        )
    except Exception:
        write_frame(
            writer,
            _fixed_response_payload(
                {
                    "mask": -3,
                    "profile_relations": 0,
                    "udp_send_reported": 0,
                    "descendant_mask": 0,
                    "descendant_profile_relations": 0,
                    "descendant_udp_send_reported": 0,
                    "descendant_pid": 0,
                },
                _RESPONSE_KEYS,
            ),
        )
        return 3
    try:
        (
            child,
            descendant_mask,
            descendant_profile_relations,
            descendant_udp_send_reported,
        ) = _spawn_descendant(control, _standard_handle_values())
    except Exception:
        write_frame(
            writer,
            _fixed_response_payload(
                {
                    "mask": mask,
                    "profile_relations": profile_relations,
                    "udp_send_reported": udp_send_reported,
                    "descendant_mask": -4,
                    "descendant_profile_relations": 0,
                    "descendant_udp_send_reported": 0,
                    "descendant_pid": 0,
                },
                _RESPONSE_KEYS,
            ),
        )
        return 3
    write_frame(
        writer,
        _fixed_response_payload(
            {
                "mask": mask,
                "profile_relations": profile_relations,
                "udp_send_reported": udp_send_reported,
                "descendant_mask": descendant_mask,
                "descendant_profile_relations": descendant_profile_relations,
                "descendant_udp_send_reported": descendant_udp_send_reported,
                "descendant_pid": child.pid,
            },
            _RESPONSE_KEYS,
        ),
    )
    _wait_for_job_termination()
    return 0


def main() -> int:
    if sys.argv[1:] == [_DESCENDANT_ARGUMENT]:
        return _descendant_main()
    if sys.argv[1:]:
        return 2
    return _initial_main()


if __name__ == "__main__":
    raise SystemExit(main())

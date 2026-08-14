"""Fixed, bounded probe for AppContainer TEMP traversal and write access."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from pathlib import Path

_SOURCE_ROOT = Path(__file__).parents[2] / "src"
sys.path.insert(0, str(_SOURCE_ROOT))

from paperworkshell.worker._windows_runtime import (  # noqa: E402
    read_bootstrap_from_standard_error,
    verify_runtime_isolation,
)
from paperworkshell.worker.protocol.bootstrap import decode_bootstrap_grant  # noqa: E402
from paperworkshell.worker.protocol.framing import read_frame, write_frame  # noqa: E402


def _attempt(operation: Callable[[], None]) -> dict[str, int | bool]:
    try:
        operation()
    except OSError as error:
        return {
            "ok": False,
            "errno": int(error.errno or 0),
            "winerror": int(error.winerror or 0),
        }
    return {"ok": True, "errno": 0, "winerror": 0}


def _stat(path: Path) -> None:
    path.stat()


def _list(path: Path) -> None:
    tuple(path.iterdir())


def _write_read_delete(path: Path) -> None:
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        assert os.write(descriptor, b"pws-temp-probe") == len(b"pws-temp-probe")
        assert os.lseek(descriptor, 0, os.SEEK_SET) == 0
        assert os.read(descriptor, 32) == b"pws-temp-probe"
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        path.unlink(missing_ok=True)


def _nested_write_read_delete(temp: Path) -> None:
    directory = temp / "pws-app-container-nested-probe"
    candidate = directory / "candidate.tmp"
    directory.mkdir()
    try:
        _write_read_delete(candidate)
    finally:
        candidate.unlink(missing_ok=True)
        directory.rmdir()


def _write_measurement_file(path: Path) -> int:
    payload = b"pws-temp-measurement"
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        assert os.write(descriptor, payload) == len(payload)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return len(payload)


def main() -> int:
    grant = decode_bootstrap_grant(read_bootstrap_from_standard_error())
    reader = getattr(sys.stdin.buffer, "raw", sys.stdin.buffer)
    writer = getattr(sys.stdout.buffer, "raw", sys.stdout.buffer)
    verify_runtime_isolation()
    if read_frame(reader) != b"probe":
        return 2
    local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
    temp = Path(os.environ.get("TEMP", ""))
    candidate = temp / "pws-app-container-temp-probe.tmp"
    measurement = temp / f"pws-app-container-measurement-{grant.case_id.hex}.tmp"
    pycache_candidate = (
        Path(str(sys.pycache_prefix)) / f"pws-pycache-mutation-{grant.case_id.hex}.tmp"
    )
    try:
        response = {
            "active_cache_list": _attempt(lambda: _list(Path(str(sys.pycache_prefix)))),
            "active_cache_stat": _attempt(lambda: _stat(Path(str(sys.pycache_prefix)))),
            "local_name": local_app_data.name,
            "temp_name": temp.name,
            "temp_parent_matches_local": temp.parent == local_app_data,
            "initial_local_stat": _attempt(lambda: _stat(local_app_data)),
            "initial_temp_stat": _attempt(lambda: _stat(temp)),
            "materialize": _attempt(lambda: temp.mkdir(parents=True, exist_ok=True)),
            "materialized_local_is_dir": local_app_data.is_dir(),
            "materialized_temp_is_dir": temp.is_dir(),
            "materialized_local_stat": _attempt(lambda: _stat(local_app_data)),
            "materialized_local_list": _attempt(lambda: _list(local_app_data)),
            "materialized_temp_stat": _attempt(lambda: _stat(temp)),
            "materialized_temp_list": _attempt(lambda: _list(temp)),
            "temp_round_trip": _attempt(lambda: _write_read_delete(candidate)),
            "nested_temp_round_trip": _attempt(lambda: _nested_write_read_delete(temp)),
            "pycache_mutation": _attempt(lambda: _write_read_delete(pycache_candidate)),
            "measurement_bytes": _write_measurement_file(measurement),
        }
        candidate.unlink(missing_ok=True)
        write_frame(
            writer,
            json.dumps(response, sort_keys=True, separators=(",", ":")).encode("ascii"),
        )
        if read_frame(reader) != b"release":
            return 3
        measurement.unlink()
        write_frame(writer, b"released")
    finally:
        candidate.unlink(missing_ok=True)
        measurement.unlink(missing_ok=True)
        pycache_candidate.unlink(missing_ok=True)
    del grant
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

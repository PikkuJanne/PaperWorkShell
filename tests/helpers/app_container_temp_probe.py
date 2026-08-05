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


def main() -> int:
    grant = decode_bootstrap_grant(read_bootstrap_from_standard_error())
    verify_runtime_isolation()
    reader = getattr(sys.stdin.buffer, "raw", sys.stdin.buffer)
    writer = getattr(sys.stdout.buffer, "raw", sys.stdout.buffer)
    if read_frame(reader) != b"probe":
        return 2
    local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
    temp = Path(os.environ.get("TEMP", ""))
    candidate = temp / "pws-app-container-temp-probe.tmp"
    response = {
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
    }
    candidate.unlink(missing_ok=True)
    write_frame(
        writer,
        json.dumps(response, sort_keys=True, separators=(",", ":")).encode("ascii"),
    )
    del grant
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

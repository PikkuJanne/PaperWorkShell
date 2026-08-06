"""Fixed live probe for the trusted qpdf AppContainer resource."""

from __future__ import annotations

import json
import os
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

_QPDF_ENVIRONMENT = "PWS_QPDF_EXECUTABLE"


def _probe_qpdf() -> dict[str, bool | int]:
    configured = os.environ.get(_QPDF_ENVIRONMENT)
    if configured is None:
        return {"configured": False, "started": False, "version_match": False, "status": 1}
    executable = Path(configured)
    environment = {
        name: value
        for name in ("SYSTEMROOT", "WINDIR", "LOCALAPPDATA", "TEMP", "TMP")
        if (value := os.environ.get(name)) is not None
    }
    try:
        completed = subprocess.run(
            (os.fspath(executable), "--version"),
            check=False,
            cwd=executable.parent,
            env=environment,
            shell=False,
            capture_output=True,
            timeout=10.0,
        )
    except OSError as error:
        status = getattr(error, "winerror", None)
        return {
            "configured": True,
            "started": False,
            "version_match": False,
            "status": status if type(status) is int and status > 0 else 2,
        }
    except subprocess.SubprocessError:
        return {"configured": True, "started": False, "version_match": False, "status": 3}
    return {
        "configured": True,
        "started": True,
        "version_match": completed.stdout.startswith(b"qpdf version 12.3.2\r\n"),
        "status": completed.returncode & 0xFFFF_FFFF,
    }


def main() -> int:
    grant = decode_bootstrap_grant(read_bootstrap_from_standard_error())
    del grant
    evidence = verify_runtime_isolation()
    result = {
        "app_container": evidence.app_container,
        "in_job": evidence.in_job,
        "minimal_environment": evidence.minimal_environment,
        **_probe_qpdf(),
    }
    writer = getattr(sys.stdout.buffer, "raw", sys.stdout.buffer)
    write_frame(
        writer,
        json.dumps(result, sort_keys=True, separators=(",", ":")).encode("ascii"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

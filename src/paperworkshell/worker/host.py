"""Fixed document-worker host entry point."""

from __future__ import annotations

import sys

from paperworkshell.worker._windows_runtime import (
    IsolationAuditError,
    read_bootstrap_from_standard_error,
    verify_runtime_isolation,
)
from paperworkshell.worker.protocol.bootstrap import BootstrapError, decode_bootstrap_grant
from paperworkshell.worker.runtime import HostExitCode, WorkerHostRuntime


def main() -> int:
    """Start one isolated worker session without emitting sensitive diagnostics."""

    try:
        bootstrap = read_bootstrap_from_standard_error()
        grant = decode_bootstrap_grant(bootstrap)
    except BootstrapError, IsolationAuditError:
        return int(HostExitCode.BOOTSTRAP_FAILED)
    try:
        verify_runtime_isolation()
    except IsolationAuditError:
        return int(HostExitCode.ISOLATION_FAILED)
    reader = getattr(sys.stdin.buffer, "raw", sys.stdin.buffer)
    writer = getattr(sys.stdout.buffer, "raw", sys.stdout.buffer)
    return int(WorkerHostRuntime(grant, reader, writer).run())


if __name__ == "__main__":
    raise SystemExit(main())

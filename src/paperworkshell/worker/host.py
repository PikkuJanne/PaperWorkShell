"""Fixed document-worker host entry point."""

from __future__ import annotations

import sys

from paperworkshell.worker._windows_runtime import (
    IsolationAuditError,
    read_bootstrap_from_standard_error,
    verify_runtime_isolation,
)
from paperworkshell.worker.operations import NoopOperationHandler, RuntimeOperationRegistry
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
    try:
        # Delay the optional native engine import until bootstrap/isolation have
        # succeeded.  A staged noop-only test runtime still fails closed if the
        # PDF engine is unavailable, without emitting an import traceback.
        from paperworkshell.worker.pdf_operation import build_pdf_runtime_registry

        operations = build_pdf_runtime_registry()
    except Exception:
        # Never advertise a document capability whose native adapter failed to
        # load. The client will reject its required-capability negotiation.
        operations = RuntimeOperationRegistry((NoopOperationHandler(),))
    runtime = WorkerHostRuntime(grant, reader, writer, operations=operations)
    return int(runtime.run())


if __name__ == "__main__":
    raise SystemExit(main())

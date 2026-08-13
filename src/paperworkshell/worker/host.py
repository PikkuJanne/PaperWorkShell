"""Fixed document-worker host entry point."""

from __future__ import annotations

import platform
import sys

from paperworkshell.worker._windows_runtime import (
    IsolationAuditError,
    read_bootstrap_from_standard_error,
    verify_runtime_isolation,
)
from paperworkshell.worker.operations import (
    NoopOperationHandler,
    OperationHandler,
    RuntimeOperationRegistry,
)
from paperworkshell.worker.process_creation_policy import (
    WorkerProcessCreationDenied,
    install_worker_process_creation_policy,
)
from paperworkshell.worker.protocol.bootstrap import BootstrapError, decode_bootstrap_grant
from paperworkshell.worker.runtime import HostExitCode, WorkerHostRuntime


def _cache_windows_platform_family() -> None:
    """Prevent the stdlib from attempting its blocked ``cmd /c ver`` fallback."""

    platform._uname_cache = platform.uname_result("Windows", "", "", "", "")  # type: ignore[attr-defined]


def main() -> int:
    """Start one isolated worker session without emitting sensitive diagnostics."""

    try:
        bootstrap = read_bootstrap_from_standard_error()
        grant = decode_bootstrap_grant(bootstrap)
    except BootstrapError, IsolationAuditError:
        return int(HostExitCode.BOOTSTRAP_FAILED)
    try:
        verify_runtime_isolation()
        # The AppContainer blocks the stdlib uname fallback's ambient
        # ``cmd /c ver`` probe. Cache only the already-proven OS family before
        # the immutable process allowlist is installed and adapters import.
        _cache_windows_platform_family()
        install_worker_process_creation_policy()
    except IsolationAuditError, WorkerProcessCreationDenied:
        return int(HostExitCode.ISOLATION_FAILED)
    reader = getattr(sys.stdin.buffer, "raw", sys.stdin.buffer)
    writer = getattr(sys.stdout.buffer, "raw", sys.stdout.buffer)
    # Delay optional native-engine imports until bootstrap and isolation succeed.
    # Each capability is loaded independently, so one unavailable adapter cannot
    # cause a different, working document capability to be advertised or hidden.
    operations = _build_operation_registry()
    runtime = WorkerHostRuntime(grant, reader, writer, operations=operations)
    return int(runtime.run())


def _build_operation_registry() -> RuntimeOperationRegistry:
    """Load only independently admitted document-operation handlers."""

    handlers: list[OperationHandler] = [NoopOperationHandler()]
    try:
        from paperworkshell.worker.image_normalize_operation import (
            build_image_normalize_operation_handler,
        )

        handlers.append(build_image_normalize_operation_handler())
    except Exception:
        pass
    try:
        from paperworkshell.worker.image_transform_operation import (
            build_image_transform_operation_handler,
        )

        handlers.append(build_image_transform_operation_handler())
    except Exception:
        pass
    try:
        from paperworkshell.worker.pdf_operation import (
            build_pdf_inspection_operation_handler,
        )

        handlers.append(build_pdf_inspection_operation_handler())
    except Exception:
        pass
    try:
        from paperworkshell.worker.pdf_thumbnail_operation import (
            build_pdf_thumbnail_operation_handler,
        )

        handlers.append(build_pdf_thumbnail_operation_handler())
    except Exception:
        pass
    try:
        from paperworkshell.worker.pdf_export_operation import (
            build_pdf_export_operation_handler,
        )

        handlers.append(build_pdf_export_operation_handler())
    except Exception:
        pass
    try:
        from paperworkshell.worker.ocr_operation import (
            build_ocr_process_operation_handler,
        )

        ocr_handler = build_ocr_process_operation_handler()
        if ocr_handler is not None:
            handlers.append(ocr_handler)
    except Exception:
        pass
    # Never advertise a document capability whose adapter failed to load. The
    # client rejects negotiation when its required capability is unavailable.
    return RuntimeOperationRegistry(tuple(handlers))


if __name__ == "__main__":
    raise SystemExit(main())

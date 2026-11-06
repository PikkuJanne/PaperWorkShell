"""Use the existing authenticated Windows no-replace publication boundary."""

from __future__ import annotations

import time
from collections.abc import Callable
from io import BytesIO
from pathlib import Path

from paperworkshell.diagnostics.bundle import DiagnosticBundlePreview
from paperworkshell.diagnostics.errors import (
    DiagnosticError,
    DiagnosticErrorCode,
    DiagnosticErrorContext,
    reject_diagnostic,
)
from paperworkshell.domain.resource_policy import ResourcePolicy
from paperworkshell.storage.export_destination import (
    ExportCommitState,
    ExportDestinationError,
    ExportDestinationErrorCode,
    WindowsExportDestination,
)


class WindowsDiagnosticBundleWriter:
    """One bounded byte snapshot, same-folder stage, verified no-replace commit."""

    def __init__(
        self,
        policy: ResourcePolicy,
        *,
        publisher: WindowsExportDestination | None = None,
    ) -> None:
        if type(policy) is not ResourcePolicy:
            reject_diagnostic(DiagnosticErrorCode.BUNDLE_INVALID, DiagnosticErrorContext.BUNDLE)
        if publisher is not None and type(publisher) is not WindowsExportDestination:
            reject_diagnostic(DiagnosticErrorCode.BUNDLE_INVALID, DiagnosticErrorContext.BUNDLE)
        self._policy = policy
        self._publisher = publisher or WindowsExportDestination(
            minimum_free_space_reserve_bytes=policy.minimum_export_free_space_reserve_bytes
        )

    def write(
        self,
        preview: DiagnosticBundlePreview,
        destination: Path,
        *,
        cancellation: Callable[[], bool],
    ) -> None:
        """Publish exactly the preview hash; never read a case or source file."""

        try:
            if type(preview) is not DiagnosticBundlePreview:
                reject_diagnostic(DiagnosticErrorCode.BUNDLE_INVALID, DiagnosticErrorContext.BUNDLE)
            preview.validate()
            if len(preview.archive) > self._policy.maximum_temporary_storage_bytes:
                reject_diagnostic(DiagnosticErrorCode.BUNDLE_INVALID, DiagnosticErrorContext.BUNDLE)
            end = time.monotonic() + self._policy.worker_cpu_time_ms / 1000
            result = self._publisher.commit(
                destination,
                BytesIO(preview.archive),
                expected_byte_size=len(preview.archive),
                expected_sha256=preview.sha256,
                cancellation=cancellation,
                deadline=lambda: time.monotonic() >= end,
            )
            if result.state is ExportCommitState.DESTINATION_EXISTS:
                reject_diagnostic(
                    DiagnosticErrorCode.DESTINATION_EXISTS, DiagnosticErrorContext.BUNDLE
                )
            if (
                result.state is not ExportCommitState.COMMITTED
                or result.sha256 != preview.sha256
                or result.byte_size != len(preview.archive)
            ):
                reject_diagnostic(DiagnosticErrorCode.EXPORT_FAILED, DiagnosticErrorContext.BUNDLE)
        except DiagnosticError:
            raise
        except ExportDestinationError as error:
            code = (
                DiagnosticErrorCode.CANCELLED
                if error.code is ExportDestinationErrorCode.CANCELLED
                else DiagnosticErrorCode.EXPORT_FAILED
            )
            reject_diagnostic(code, DiagnosticErrorContext.BUNDLE)
        except Exception:
            reject_diagnostic(DiagnosticErrorCode.EXPORT_FAILED, DiagnosticErrorContext.BUNDLE)

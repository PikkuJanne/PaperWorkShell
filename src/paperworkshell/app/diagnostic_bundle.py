"""Opt-in local support export through a narrow destination port."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from paperworkshell.diagnostics.bundle import DiagnosticBundlePreview
from paperworkshell.diagnostics.errors import (
    DiagnosticError,
    DiagnosticErrorCode,
    DiagnosticErrorContext,
    reject_diagnostic,
)
from paperworkshell.diagnostics.logging import SanitizedDiagnosticLogger


class DiagnosticBundleWriter(Protocol):
    """Publish only the reviewed snapshot; paths are never diagnostic fields."""

    def write(
        self,
        preview: DiagnosticBundlePreview,
        destination: Path,
        *,
        cancellation: Callable[[], bool],
    ) -> None: ...


class DiagnosticBundleService:
    """Read only an already-sanitized logger, never private case files or logs."""

    def __init__(self, logger: SanitizedDiagnosticLogger, writer: DiagnosticBundleWriter) -> None:
        if type(logger) is not SanitizedDiagnosticLogger:
            reject_diagnostic(DiagnosticErrorCode.BUNDLE_INVALID, DiagnosticErrorContext.BUNDLE)
        self._logger = logger
        self._writer = writer

    def preview(self, *, generated_at: datetime | None = None) -> DiagnosticBundlePreview:
        """Atomically freeze existing approved fields without writing any file."""

        try:
            return DiagnosticBundlePreview(
                self._logger.snapshot_summary(generated_at=generated_at or datetime.now(UTC))
            )
        except DiagnosticError:
            raise
        except Exception:
            reject_diagnostic(DiagnosticErrorCode.BUNDLE_INVALID, DiagnosticErrorContext.BUNDLE)

    def save(
        self,
        preview: DiagnosticBundlePreview,
        destination: Path,
        *,
        consent: bool,
        cancellation: Callable[[], bool],
    ) -> None:
        """Require explicit consent to this exact snapshot before writer access."""

        if consent is not True:
            reject_diagnostic(DiagnosticErrorCode.CONSENT_REQUIRED, DiagnosticErrorContext.BUNDLE)
        try:
            if type(preview) is not DiagnosticBundlePreview:
                reject_diagnostic(DiagnosticErrorCode.BUNDLE_INVALID, DiagnosticErrorContext.BUNDLE)
            preview.validate()
            if cancellation():
                reject_diagnostic(DiagnosticErrorCode.CANCELLED, DiagnosticErrorContext.BUNDLE)
            self._writer.write(preview, destination, cancellation=cancellation)
        except DiagnosticError:
            raise
        except Exception:
            reject_diagnostic(DiagnosticErrorCode.EXPORT_FAILED, DiagnosticErrorContext.BUNDLE)

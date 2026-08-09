"""Toolkit-free orchestration for bounded startup case recovery."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from paperworkshell.app.export_history import (
    PdfExportHistoryService,
    PdfExportRetryRecoveryService,
)
from paperworkshell.diagnostics.models import (
    DiagnosticCount,
    DiagnosticCountKind,
    DiagnosticEventCode,
    DiagnosticFailureCode,
    DiagnosticOutcome,
    DiagnosticRecord,
    DiagnosticResource,
    DiagnosticResourceKind,
)
from paperworkshell.domain.recovery import (
    RecoveryError,
    RecoveryErrorCode,
    StartupRecoveryResult,
    StartupRecoveryStatus,
    reject_recovery,
)
from paperworkshell.storage.export_history import WindowsPdfExportHistoryRepository
from paperworkshell.storage.export_retry import WindowsPdfExportRetryRepository
from paperworkshell.storage.manifest_repository import WindowsManifestRepository
from paperworkshell.storage.recovery import WindowsStartupRecoveryStore
from paperworkshell.storage.workspace import CaseWorkspaceManager


class StartupRecoveryStore(Protocol):
    """Narrow platform port for one bounded startup scan."""

    def scan_and_recover(self) -> StartupRecoveryResult: ...


@runtime_checkable
class StartupRecoveryDiagnosticSink(Protocol):
    """Receive one already-sanitized structured recovery record."""

    def emit(self, value: DiagnosticRecord) -> None: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


class StartupRecoveryService:
    """Expose one validated, UI-safe startup recovery result."""

    __slots__ = ("_clock", "_diagnostics", "_store")

    def __init__(
        self,
        store: StartupRecoveryStore,
        *,
        diagnostics: StartupRecoveryDiagnosticSink | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if diagnostics is not None and not isinstance(
            diagnostics,
            StartupRecoveryDiagnosticSink,
        ):
            reject_recovery(RecoveryErrorCode.MODEL_INVALID, "recovery.diagnostics")
        if not callable(clock):
            reject_recovery(RecoveryErrorCode.MODEL_INVALID, "recovery.clock")
        self._store = store
        self._diagnostics = diagnostics
        self._clock = clock

    def recover_startup(self) -> StartupRecoveryResult:
        """Scan local cases and return only validated content-free recovery facts."""

        self._emit(
            DiagnosticEventCode.RECOVERY_SCAN_STARTED,
            outcome=None,
            failure_code=None,
        )
        try:
            result = self._store.scan_and_recover()
        except RecoveryError:
            self._emit_failure()
            raise
        except Exception:
            self._emit_failure()
            reject_recovery(RecoveryErrorCode.INTERNAL_ERROR, "recovery.store")
        if type(result) is not StartupRecoveryResult:
            self._emit_failure()
            reject_recovery(RecoveryErrorCode.INTERNAL_ERROR, "recovery.result")
        attention = result.status is StartupRecoveryStatus.ATTENTION_REQUIRED
        self._emit(
            DiagnosticEventCode.RECOVERY_SCAN_COMPLETED,
            outcome=DiagnosticOutcome.PARTIAL if attention else DiagnosticOutcome.SUCCESS,
            failure_code=(DiagnosticFailureCode.RECOVERY_ATTENTION_REQUIRED if attention else None),
            result=result,
        )
        return result

    def _emit_failure(self) -> None:
        self._emit(
            DiagnosticEventCode.RECOVERY_SCAN_COMPLETED,
            outcome=DiagnosticOutcome.FAILED,
            failure_code=DiagnosticFailureCode.RECOVERY_SCAN_FAILED,
        )

    def _emit(
        self,
        event: DiagnosticEventCode,
        *,
        outcome: DiagnosticOutcome | None,
        failure_code: DiagnosticFailureCode | None,
        result: StartupRecoveryResult | None = None,
    ) -> None:
        if self._diagnostics is None:
            return
        try:
            occurred_at = self._clock()
            counts: tuple[DiagnosticCount, ...] = ()
            resources: tuple[DiagnosticResource, ...] = ()
            if result is not None:
                cases = result.cases
                counts = (
                    DiagnosticCount(DiagnosticCountKind.CASES_SCANNED, result.scanned_cases),
                    DiagnosticCount(
                        DiagnosticCountKind.CASES_RECOVERED,
                        result.recovered_cases,
                    ),
                    DiagnosticCount(
                        DiagnosticCountKind.CASES_UNAVAILABLE,
                        result.unavailable_cases,
                    ),
                    DiagnosticCount(
                        DiagnosticCountKind.FILES_REMOVED,
                        sum(
                            case.removed_source_orphans + case.removed_temp_files for case in cases
                        ),
                    ),
                    DiagnosticCount(
                        DiagnosticCountKind.DIRECTORIES_REMOVED,
                        sum(case.removed_temp_directories for case in cases),
                    ),
                    DiagnosticCount(
                        DiagnosticCountKind.CACHE_ENTRIES_REMOVED,
                        sum(case.removed_thumbnail_stages for case in cases),
                    ),
                    DiagnosticCount(
                        DiagnosticCountKind.WARNINGS,
                        sum(len(case.issues) for case in cases),
                    ),
                )
                resources = (
                    DiagnosticResource(
                        DiagnosticResourceKind.STORAGE_BYTES_RELEASED,
                        sum(case.released_bytes for case in cases),
                    ),
                )
            self._diagnostics.emit(
                DiagnosticRecord(
                    occurred_at=occurred_at,
                    event=event,
                    outcome=outcome,
                    failure_code=failure_code,
                    counts=counts,
                    resources=resources,
                )
            )
        except Exception:
            return


def build_windows_startup_recovery_service(
    manager: CaseWorkspaceManager,
    *,
    diagnostics: StartupRecoveryDiagnosticSink | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> StartupRecoveryService:
    """Compose the complete Windows startup path with mandatory export settlement."""

    if not isinstance(manager, CaseWorkspaceManager):
        reject_recovery(RecoveryErrorCode.MODEL_INVALID, "recovery.composition")
    manifests = WindowsManifestRepository(manager)
    history = PdfExportHistoryService(
        WindowsPdfExportHistoryRepository(manager),
        manifests,
    )
    retries = WindowsPdfExportRetryRepository(manager)
    exports = PdfExportRetryRecoveryService(history, retries)
    store = WindowsStartupRecoveryStore(
        manager,
        settle_exports=exports.recover_case,
    )
    return StartupRecoveryService(
        store,
        diagnostics=diagnostics,
        clock=clock,
    )

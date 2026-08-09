from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.app.recovery import StartupRecoveryService, StartupRecoveryStore
from paperworkshell.diagnostics.logging import SanitizedDiagnosticLogger
from paperworkshell.diagnostics.models import (
    DiagnosticCountKind,
    DiagnosticEventCode,
    DiagnosticFailureCode,
    DiagnosticOutcome,
)
from paperworkshell.domain.recovery import (
    CaseRecoveryResult,
    CaseRecoveryStatus,
    RecoveryError,
    RecoveryErrorCode,
    StartupRecoveryResult,
)

CASE_ID = UUID("30000000-0000-4000-8000-000000000003")
NOW = datetime(2026, 8, 9, 13, 14, 15, tzinfo=UTC)


@dataclass
class _Store:
    value: object

    def scan_and_recover(self) -> StartupRecoveryResult:
        if isinstance(self.value, BaseException):
            raise self.value
        return cast(StartupRecoveryResult, self.value)


def test_service_returns_the_exact_validated_store_result() -> None:
    expected = StartupRecoveryResult((CaseRecoveryResult(CASE_ID, CaseRecoveryStatus.CURRENT, 0),))

    result = StartupRecoveryService(_Store(expected)).recover_startup()

    assert result is expected


def test_service_maps_raw_store_failure_without_retaining_sensitive_context() -> None:
    prohibited = "C:\\private\\applicant-name.pdf"
    service = StartupRecoveryService(_Store(RuntimeError(prohibited)))

    with pytest.raises(RecoveryError) as raised:
        service.recover_startup()

    assert raised.value.code is RecoveryErrorCode.INTERNAL_ERROR
    assert raised.value.context == "recovery.store"
    assert prohibited not in str(raised.value)
    assert prohibited not in repr(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_service_rejects_a_non_result_and_preserves_control_interruptions() -> None:
    with pytest.raises(RecoveryError) as invalid:
        StartupRecoveryService(cast(StartupRecoveryStore, _Store(object()))).recover_startup()
    assert invalid.value.code is RecoveryErrorCode.INTERNAL_ERROR
    assert invalid.value.context == "recovery.result"

    interruption = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt) as raised:
        StartupRecoveryService(_Store(interruption)).recover_startup()
    assert raised.value is interruption


def test_service_emits_only_typed_aggregate_recovery_diagnostics() -> None:
    expected = StartupRecoveryResult(
        (
            CaseRecoveryResult(
                CASE_ID,
                CaseRecoveryStatus.RECOVERED,
                2,
                removed_source_orphans=1,
                removed_temp_files=2,
                removed_temp_directories=1,
                released_bytes=512,
            ),
        )
    )
    diagnostics = SanitizedDiagnosticLogger()

    result = StartupRecoveryService(
        _Store(expected),
        diagnostics=diagnostics,
        clock=lambda: NOW,
    ).recover_startup()

    assert result is expected
    records = diagnostics.snapshot_records()
    assert tuple(record.event for record in records) == (
        DiagnosticEventCode.RECOVERY_SCAN_STARTED,
        DiagnosticEventCode.RECOVERY_SCAN_COMPLETED,
    )
    completed = records[-1]
    assert completed.outcome is DiagnosticOutcome.SUCCESS
    assert completed.failure_code is None
    counts = {count.kind: count.value for count in completed.counts}
    assert counts[DiagnosticCountKind.CASES_SCANNED] == 1
    assert counts[DiagnosticCountKind.CASES_RECOVERED] == 1
    assert counts[DiagnosticCountKind.FILES_REMOVED] == 3


def test_service_logs_closed_failure_without_changing_sanitized_error() -> None:
    diagnostics = SanitizedDiagnosticLogger()
    service = StartupRecoveryService(
        _Store(RuntimeError("C:\\private\\diagnostic-canary.pdf")),
        diagnostics=diagnostics,
        clock=lambda: NOW,
    )

    with pytest.raises(RecoveryError):
        service.recover_startup()

    completed = diagnostics.snapshot_records()[-1]
    assert completed.event is DiagnosticEventCode.RECOVERY_SCAN_COMPLETED
    assert completed.outcome is DiagnosticOutcome.FAILED
    assert completed.failure_code is DiagnosticFailureCode.RECOVERY_SCAN_FAILED
    assert b"diagnostic-canary" not in b"".join(diagnostics.snapshot_lines())

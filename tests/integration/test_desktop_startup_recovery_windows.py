from __future__ import annotations

import sys
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

import paperworkshell.__main__ as desktop
from paperworkshell.domain.recovery import (
    RecoveryError,
    RecoveryErrorCode,
    StartupRecoveryResult,
)
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.workspace import CaseWorkspaceManager

CASE_ID = UUID("86000000-0000-4000-8000-000000000001")
TEMP_ID = UUID("87000000-0000-4000-8000-000000000001")


def _quota() -> WorkspaceQuota:
    return WorkspaceQuota(
        maximum_case_bytes=32_000_000,
        maximum_cache_bytes=4_000_000,
        maximum_temp_bytes=8_000_000,
        maximum_entries=1_024,
        maximum_depth=16,
    )


class _Composition:
    source_inspector = object()
    thumbnail_generator = object()

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_recovery_failure_prevents_worker_composition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_composed: list[bool] = []

    class _FailedRecovery:
        def recover_startup(self) -> StartupRecoveryResult:
            raise RecoveryError(RecoveryErrorCode.SCAN_FAILED, "recovery.test")

    monkeypatch.setattr(
        desktop,
        "build_windows_startup_recovery_service",
        lambda _manager: _FailedRecovery(),
    )
    monkeypatch.setattr(
        desktop,
        "create_desktop_worker_composition",
        lambda *_args, **_kwargs: worker_composed.append(True),
    )

    with pytest.raises(RecoveryError) as raised:
        desktop._build_backend(cast(CaseWorkspaceManager, object()))

    assert raised.value.code is RecoveryErrorCode.SCAN_FAILED
    assert worker_composed == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows startup recovery integration")
def test_cold_desktop_restart_recovers_before_worker_composition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = (tmp_path / "profile").resolve()
    profile.mkdir()
    first_manager = CaseWorkspaceManager(
        profile_root=profile,
        quota=_quota(),
        id_factory=lambda: CASE_ID,
    )
    lease = WindowsCaseLifecycleStore(first_manager).create_case()
    lease.close()
    stale_temp = first_manager.layout(CASE_ID).temp / f"{TEMP_ID}.tmp"
    stale_temp.write_bytes(b"synthetic-leftover-work")

    fresh_manager = CaseWorkspaceManager(profile_root=profile, quota=_quota())
    composition = _Composition()
    composition_observations: list[bool] = []

    def compose(*_args: object, **_kwargs: object) -> _Composition:
        composition_observations.append(stale_temp.exists())
        return composition

    monkeypatch.setattr(desktop, "_desktop_worker_config", object)
    monkeypatch.setattr(desktop, "create_desktop_worker_composition", compose)

    backend = desktop._build_backend(fresh_manager)
    try:
        result = backend.startup_recovery_result
        assert result.scanned_cases == 1
        assert result.cases[0].case_id == CASE_ID
        assert result.cases[0].removed_temp_files == 1
        assert result.cases[0].released_bytes == len(b"synthetic-leftover-work")
        assert not stale_temp.exists()
        assert composition_observations == [False]
    finally:
        backend.shutdown()

    assert composition.closed

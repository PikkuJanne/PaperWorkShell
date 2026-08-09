from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from paperworkshell.domain.recovery import RecoveryIssueCode
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.recovery import WindowsStartupRecoveryStore
from paperworkshell.storage.workspace import CaseWorkspaceManager

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="PWS-0113 recovery containment targets supported Windows volumes",
)

CASE_ID = UUID("51000000-0000-4000-8000-000000000001")
ORPHAN_ID = UUID("52000000-0000-4000-8000-000000000002")
TEMP_ID = UUID("53000000-0000-4000-8000-000000000003")


def _quota() -> WorkspaceQuota:
    return WorkspaceQuota(
        maximum_case_bytes=16_000_000,
        maximum_cache_bytes=4_000_000,
        maximum_temp_bytes=4_000_000,
        maximum_entries=256,
        maximum_depth=16,
    )


def test_recovery_never_follows_hardlinked_orphans_outside_the_case(
    tmp_path: Path,
) -> None:
    private_token = "pws-private-applicant-address-canary"
    profile = (tmp_path / "profile").resolve()
    profile.mkdir()
    manager = CaseWorkspaceManager(
        profile_root=profile,
        quota=_quota(),
        id_factory=lambda: CASE_ID,
    )
    lease = WindowsCaseLifecycleStore(
        manager,
        clock=lambda: datetime(2026, 8, 9, 11, 0, tzinfo=UTC),
    ).create_case()
    lease.close()
    layout = manager.layout(CASE_ID)
    outside = (tmp_path / f"{private_token}.pdf").resolve()
    outside_payload = b"synthetic-outside-sentinel"
    outside.write_bytes(outside_payload)
    source_alias = layout.originals / f"{ORPHAN_ID}.bin"
    temp_alias = layout.temp / f"{TEMP_ID}.tmp"
    try:
        os.link(outside, source_alias)
        os.link(outside, temp_alias)
    except OSError as error:
        source_alias.unlink(missing_ok=True)
        temp_alias.unlink(missing_ok=True)
        pytest.skip(f"local hard-link capability unavailable: {error.winerror}")

    result = WindowsStartupRecoveryStore(manager).scan_and_recover()

    assert outside.read_bytes() == outside_payload
    assert source_alias.read_bytes() == outside_payload
    assert temp_alias.read_bytes() == outside_payload
    case = result.cases[0]
    assert case.retained_source_artifacts == 1
    assert case.removed_source_orphans == 0
    assert RecoveryIssueCode.SOURCE_ARTIFACTS_RETAINED in case.issues
    assert RecoveryIssueCode.TEMP_CLEANUP_PARTIAL in case.issues
    rendered = f"{result!r}\n{case!r}\n{case.issues!r}"
    assert private_token not in rendered
    assert os.fspath(outside) not in rendered

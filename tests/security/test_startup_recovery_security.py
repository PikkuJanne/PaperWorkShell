from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from paperworkshell.app.recovery import build_windows_startup_recovery_service
from paperworkshell.domain.pdf_export_history import PdfExportHistoryError
from paperworkshell.domain.recovery import RecoveryIssueCode
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.export_retry import WindowsPdfExportRetryRepository
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.workspace import CaseWorkspaceManager

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="PWS-0113 recovery containment targets supported Windows volumes",
)

CASE_ID = UUID("51000000-0000-4000-8000-000000000001")
ORPHAN_ID = UUID("52000000-0000-4000-8000-000000000002")
TEMP_ID = UUID("53000000-0000-4000-8000-000000000003")
EXPORT_ID = UUID("54000000-0000-4000-8000-000000000004")


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

    result = build_windows_startup_recovery_service(manager).recover_startup()

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


@pytest.mark.parametrize("alias_kind", ("hardlink", "symlink"))
def test_export_retry_alias_is_retained_without_touching_outside_sentinel(
    tmp_path: Path,
    alias_kind: str,
) -> None:
    profile = (tmp_path / "profile").resolve()
    profile.mkdir()
    manager = CaseWorkspaceManager(
        profile_root=profile,
        quota=_quota(),
        id_factory=lambda: CASE_ID,
    )
    lease = WindowsCaseLifecycleStore(manager).create_case()
    lease.close()
    outside = (tmp_path / "private-export-retry-sentinel.pdf").resolve()
    outside_payload = b"synthetic-outside-retry-sentinel"
    outside.write_bytes(outside_payload)
    marker = manager.layout(CASE_ID).journal / f"export-retry-{EXPORT_ID}.committed.json"
    try:
        if alias_kind == "hardlink":
            os.link(outside, marker)
        else:
            os.symlink(outside, marker, target_is_directory=False)
    except OSError as error:
        marker.unlink(missing_ok=True)
        pytest.skip(f"local {alias_kind} capability unavailable: {error.winerror}")

    retries = WindowsPdfExportRetryRepository(manager)
    inventory = retries.inventory(CASE_ID)
    assert inventory.invalid_export_ids == (EXPORT_ID,)
    assert inventory.invalid_entries == 1
    try:
        discarded = retries.discard_committed(CASE_ID, EXPORT_ID, "a" * 64)
    except PdfExportHistoryError:
        discarded = False
    assert not discarded

    startup = build_windows_startup_recovery_service(manager).recover_startup()

    assert startup.cases[0].invalid_export_retries == 1
    assert RecoveryIssueCode.EXPORT_RETRY_INVALID in startup.cases[0].issues
    assert outside.read_bytes() == outside_payload
    assert os.path.lexists(marker)
    assert retries.inventory(CASE_ID).invalid_export_ids == (EXPORT_ID,)


def test_export_retry_alternate_stream_is_isolated_without_deletion(tmp_path: Path) -> None:
    profile = (tmp_path / "profile").resolve()
    profile.mkdir()
    manager = CaseWorkspaceManager(
        profile_root=profile,
        quota=_quota(),
        id_factory=lambda: CASE_ID,
    )
    lease = WindowsCaseLifecycleStore(manager).create_case()
    lease.close()
    marker = manager.layout(CASE_ID).journal / f"export-retry-{EXPORT_ID}.committed.json"
    marker_payload = b"synthetic-invalid-retry-marker"
    stream_payload = b"synthetic-private-stream-sentinel"
    marker.write_bytes(marker_payload)
    stream = Path(f"{marker}:private")
    try:
        stream.write_bytes(stream_payload)
    except OSError as error:
        marker.unlink(missing_ok=True)
        pytest.skip(f"alternate streams unavailable: {error.winerror}")

    retries = WindowsPdfExportRetryRepository(manager)
    inventory = retries.inventory(CASE_ID)
    assert inventory.invalid_export_ids == (EXPORT_ID,)
    assert inventory.invalid_entries == 1

    startup = build_windows_startup_recovery_service(manager).recover_startup()

    assert startup.cases[0].invalid_export_retries == 1
    assert marker.read_bytes() == marker_payload
    assert stream.read_bytes() == stream_payload
    assert retries.inventory(CASE_ID).invalid_export_ids == (EXPORT_ID,)

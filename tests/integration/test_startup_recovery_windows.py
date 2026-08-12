from __future__ import annotations

import copy
import hashlib
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.__main__ import build_windows_startup_recovery_service
from paperworkshell.diagnostics.logging import SanitizedDiagnosticLogger
from paperworkshell.diagnostics.models import (
    DiagnosticCountKind,
    DiagnosticEventCode,
    DiagnosticResourceKind,
)
from paperworkshell.domain.pdf_rendering import PdfRenderRotation
from paperworkshell.domain.pdf_thumbnails import PdfThumbnailCacheKey
from paperworkshell.domain.recovery import (
    CaseRecoveryStatus,
    ExportHistoryRecoveryResult,
    RecoveryIssueCode,
    StartupRecoveryStatus,
)
from paperworkshell.storage import recovery as recovery_module
from paperworkshell.storage._windows import WindowsHandle
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.errors import WorkspaceErrorCode, reject
from paperworkshell.storage.manifest_repository import WindowsManifestRepository
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.recovery import WindowsStartupRecoveryStore
from paperworkshell.storage.thumbnail_cache import (
    ThumbnailCacheLookupStatus,
    WindowsThumbnailCacheStore,
)
from paperworkshell.storage.workspace import (
    AuthenticatedCase,
    CaseWorkspaceManager,
    CaseWorkspacePaths,
)

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="PWS-0113 startup storage recovery targets supported Windows volumes",
)

CASE_ID = UUID("41000000-0000-4000-8000-000000000001")
ORPHAN_ID = UUID("42000000-0000-4000-8000-000000000002")
SECOND_ORPHAN_ID = UUID("42000000-0000-4000-8000-000000000003")
THUMBNAIL_STAGE_ID = UUID("43000000-0000-4000-8000-000000000003")
TEMP_STAGE_ID = UUID("44000000-0000-4000-8000-000000000004")
SOURCE_ID = UUID("45000000-0000-4000-8000-000000000005")
PAGE_ID = UUID("46000000-0000-4000-8000-000000000006")
CREATED_AT = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)


def _quota() -> WorkspaceQuota:
    return WorkspaceQuota(
        maximum_case_bytes=32_000_000,
        maximum_cache_bytes=8_000_000,
        maximum_temp_bytes=8_000_000,
        maximum_entries=512,
        maximum_depth=16,
    )


def _stack(tmp_path: Path) -> tuple[CaseWorkspaceManager, CaseWorkspacePaths]:
    profile = (tmp_path / "profile").resolve()
    profile.mkdir()
    manager = CaseWorkspaceManager(
        profile_root=profile,
        quota=_quota(),
        id_factory=lambda: CASE_ID,
    )
    lease = WindowsCaseLifecycleStore(manager, clock=lambda: CREATED_AT).create_case()
    lease.close()
    return manager, manager.layout(CASE_ID)


def _next_manifest(document: dict[str, object]) -> dict[str, object]:
    candidate = copy.deepcopy(document)
    candidate["revision"] = 1
    candidate["updated_at"] = "2026-08-09T10:01:00Z"
    return candidate


def _publish_recoverable_checkpoint(
    manager: CaseWorkspaceManager,
    layout: CaseWorkspacePaths,
) -> bytes:
    repository = WindowsManifestRepository(manager)
    opened = repository.load(CASE_ID).snapshot
    candidate = _next_manifest(opened.document)
    repository.save(
        CASE_ID,
        candidate,
        expected_revision=opened.revision,
        expected_sha256=opened.sha256,
        expected_cas_token=opened.cas_token,
    )
    checkpoint = (layout.journal / "latest.json").read_bytes()
    layout.manifest.write_bytes(b'{"schema_version":1')
    return checkpoint


def _tree_snapshot(root: Path) -> dict[str, tuple[str, bytes]]:
    snapshot: dict[str, tuple[str, bytes]] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().casefold()):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            snapshot[relative] = ("directory", b"")
        else:
            snapshot[relative] = ("file", path.read_bytes())
    return snapshot


def test_startup_recovers_manifest_then_cleans_orphans_thumbnail_stages_and_temp(
    tmp_path: Path,
) -> None:
    manager, layout = _stack(tmp_path)
    checkpoint = _publish_recoverable_checkpoint(manager, layout)
    orphan = layout.originals / f"{ORPHAN_ID}.bin"
    thumbnail_stage = layout.thumbnails / f"{THUMBNAIL_STAGE_ID}.tmp"
    temp_stage = layout.temp / f"{TEMP_STAGE_ID}.tmp"
    orphan.write_bytes(b"synthetic-unreferenced-source")
    thumbnail_stage.write_bytes(b"synthetic-stale-thumbnail-stage")
    temp_stage.write_bytes(b"synthetic-stale-case-temp")

    result = build_windows_startup_recovery_service(manager).recover_startup()

    assert result.status is StartupRecoveryStatus.COMPLETE
    assert result.scanned_cases == result.recovered_cases == 1
    case = result.cases[0]
    assert case.case_id == CASE_ID
    assert case.status is CaseRecoveryStatus.RECOVERED
    assert case.manifest_revision == 1
    assert case.issues == ()
    assert case.removed_source_orphans == 1
    assert case.retained_source_artifacts == 0
    assert case.removed_thumbnail_stages == 1
    assert case.removed_temp_files == 1
    assert case.removed_temp_directories == 0
    assert case.released_bytes == (
        len(b"synthetic-unreferenced-source")
        + len(b"synthetic-stale-thumbnail-stage")
        + len(b"synthetic-stale-case-temp")
    )
    assert layout.manifest.read_bytes() == checkpoint
    assert not orphan.exists()
    assert not thumbnail_stage.exists()
    assert list(layout.temp.iterdir()) == []


def test_invalid_case_is_reported_unavailable_without_cleaning_any_artifact(
    tmp_path: Path,
) -> None:
    manager, layout = _stack(tmp_path)
    layout.manifest.write_bytes(b'{"schema_version":1')
    orphan = layout.originals / f"{ORPHAN_ID}.bin"
    thumbnail_stage = layout.thumbnails / f"{THUMBNAIL_STAGE_ID}.tmp"
    temp_stage = layout.temp / f"{TEMP_STAGE_ID}.tmp"
    orphan.write_bytes(b"invalid-case-source-residue")
    thumbnail_stage.write_bytes(b"invalid-case-thumbnail-residue")
    temp_stage.write_bytes(b"invalid-case-temp-residue")
    before = _tree_snapshot(layout.case_root)

    result = build_windows_startup_recovery_service(manager).recover_startup()

    assert result.status is StartupRecoveryStatus.ATTENTION_REQUIRED
    assert result.unavailable_cases == 1
    case = result.cases[0]
    assert case.status is CaseRecoveryStatus.UNAVAILABLE
    assert case.manifest_revision is None
    assert case.issues == (RecoveryIssueCode.MANIFEST_INVALID,)
    assert case.removed_source_orphans == 0
    assert case.removed_thumbnail_stages == 0
    assert case.removed_temp_files == 0
    assert _tree_snapshot(layout.case_root) == before


def test_export_receipts_settle_before_generic_temp_cleanup(tmp_path: Path) -> None:
    manager, layout = _stack(tmp_path)
    temp_stage = layout.temp / f"{TEMP_STAGE_ID}.export-retry-committed.tmp"
    temp_stage.write_bytes(b"synthetic-owner-stage")
    observed: list[UUID] = []

    def settle(case_id: UUID) -> ExportHistoryRecoveryResult:
        assert temp_stage.is_file()
        observed.append(case_id)
        return ExportHistoryRecoveryResult(
            repaired_entries=1,
            ambiguous_entries=1,
        )

    result = WindowsStartupRecoveryStore(
        manager,
        settle_exports=settle,
    ).scan_and_recover()

    assert observed == [CASE_ID]
    assert not temp_stage.exists()
    case = result.cases[0]
    assert case.status is CaseRecoveryStatus.RECOVERED
    assert case.repaired_export_history == 1
    assert case.ambiguous_export_commits == 1
    assert case.issues == (RecoveryIssueCode.EXPORT_COMMIT_AMBIGUOUS,)
    assert result.status is StartupRecoveryStatus.ATTENTION_REQUIRED


def test_export_recovery_failure_is_not_mislabeled_as_invalid_receipt(
    tmp_path: Path,
) -> None:
    manager, layout = _stack(tmp_path)
    temp_evidence = layout.temp / f"{TEMP_STAGE_ID}.tmp"
    temp_evidence.write_bytes(b"export-recovery-failure-evidence")

    def fail_settlement(_case_id: UUID) -> ExportHistoryRecoveryResult:
        raise RuntimeError("synthetic export recovery failure")

    result = WindowsStartupRecoveryStore(
        manager,
        settle_exports=fail_settlement,
    ).scan_and_recover()

    case = result.cases[0]
    assert case.status is CaseRecoveryStatus.CURRENT
    assert case.invalid_export_retries == 0
    assert case.issues == (RecoveryIssueCode.EXPORT_RECOVERY_FAILED,)
    assert temp_evidence.read_bytes() == b"export-recovery-failure-evidence"


def test_unexpected_cleanup_failure_is_not_mislabeled_as_case_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, layout = _stack(tmp_path)
    orphan = layout.originals / f"{ORPHAN_ID}.bin"
    orphan.write_bytes(b"unexpected-cleanup-source-residue")

    def fail_cleanup(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("synthetic cleanup failure")

    monkeypatch.setattr(recovery_module, "_cleanup_source_orphans", fail_cleanup)

    result = build_windows_startup_recovery_service(manager).recover_startup()

    case = result.cases[0]
    assert case.status is CaseRecoveryStatus.CURRENT
    assert case.issues == (RecoveryIssueCode.CLEANUP_FAILED,)
    assert RecoveryIssueCode.CASE_CHANGED not in case.issues
    assert orphan.read_bytes() == b"unexpected-cleanup-source-residue"


def test_later_cleanup_failure_preserves_confirmed_counts_and_stops_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, layout = _stack(tmp_path)
    orphan_payload = b"confirmed-source-cleanup-before-later-failure"
    orphan = layout.originals / f"{ORPHAN_ID}.bin"
    temp_evidence = layout.temp / f"{TEMP_STAGE_ID}.tmp"
    orphan.write_bytes(orphan_payload)
    temp_evidence.write_bytes(b"later-phase-temp-evidence")
    diagnostics = SanitizedDiagnosticLogger()

    def fail_thumbnail_cleanup(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("synthetic later-phase cleanup failure")

    monkeypatch.setattr(
        WindowsThumbnailCacheStore,
        "cleanup_stale_stages",
        fail_thumbnail_cleanup,
    )

    result = build_windows_startup_recovery_service(
        manager,
        diagnostics=diagnostics,
    ).recover_startup()

    case = result.cases[0]
    assert case.issues == (RecoveryIssueCode.CLEANUP_FAILED,)
    assert case.removed_source_orphans == 1
    assert case.released_bytes == len(orphan_payload)
    assert not orphan.exists()
    assert temp_evidence.read_bytes() == b"later-phase-temp-evidence"
    completed = next(
        record
        for record in diagnostics.snapshot_records()
        if record.event is DiagnosticEventCode.RECOVERY_SCAN_COMPLETED
    )
    counts = {item.kind: item.value for item in completed.counts}
    resources = {item.kind: item.value for item in completed.resources}
    assert counts[DiagnosticCountKind.FILES_REMOVED] == 1
    assert resources[DiagnosticResourceKind.STORAGE_BYTES_RELEASED] == len(orphan_payload)


def test_post_delete_source_reauthentication_retains_confirmed_cleanup_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, layout = _stack(tmp_path)
    orphan_payload = b"confirmed-source-cleanup-before-reauth-failure"
    orphan = layout.originals / f"{ORPHAN_ID}.bin"
    temp_evidence = layout.temp / f"{TEMP_STAGE_ID}.tmp"
    orphan.write_bytes(orphan_payload)
    temp_evidence.write_bytes(b"post-delete-reauth-temp-evidence")
    original_verify = cast(
        Callable[[WindowsHandle], None],
        recovery_module.__dict__["verify_private_acl"],
    )
    calls = 0

    def fail_second_source_root_verification(handle: WindowsHandle) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            reject(
                WorkspaceErrorCode.IDENTITY_CHANGED,
                "recovery.synthetic_reauthentication",
                "Synthetic post-delete source reauthentication failure.",
            )
        original_verify(handle)

    monkeypatch.setattr(
        recovery_module,
        "verify_private_acl",
        fail_second_source_root_verification,
    )

    result = build_windows_startup_recovery_service(manager).recover_startup()

    case = result.cases[0]
    assert calls == 2
    assert case.issues == (RecoveryIssueCode.SOURCE_ARTIFACTS_RETAINED,)
    assert case.removed_source_orphans == 1
    assert case.released_bytes == len(orphan_payload)
    assert not orphan.exists()
    assert temp_evidence.read_bytes() == b"post-delete-reauth-temp-evidence"


def test_unexpected_later_source_candidate_failure_retains_confirmed_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, layout = _stack(tmp_path)
    first_payload = b"first-confirmed-source-cleanup"
    first = layout.originals / f"{ORPHAN_ID}.bin"
    second = layout.originals / f"{SECOND_ORPHAN_ID}.bin"
    temp_evidence = layout.temp / f"{TEMP_STAGE_ID}.tmp"
    first.write_bytes(first_payload)
    second.write_bytes(b"second-source-cleanup-failure")
    temp_evidence.write_bytes(b"source-candidate-failure-temp-evidence")
    original_delete = cast(
        Callable[[Path, WindowsHandle], tuple[bool, int]],
        recovery_module.__dict__["_delete_exact_source"],
    )

    def fail_second_candidate(path: Path, root: WindowsHandle) -> tuple[bool, int]:
        if path.name == second.name:
            raise RuntimeError("synthetic later source candidate failure")
        return original_delete(path, root)

    monkeypatch.setattr(
        recovery_module,
        "_delete_exact_source",
        fail_second_candidate,
    )

    result = build_windows_startup_recovery_service(manager).recover_startup()

    case = result.cases[0]
    assert case.issues == (
        RecoveryIssueCode.CLEANUP_FAILED,
        RecoveryIssueCode.SOURCE_ARTIFACTS_RETAINED,
    )
    assert case.removed_source_orphans == 1
    assert case.retained_source_artifacts == 1
    assert case.released_bytes == len(first_payload)
    assert not first.exists()
    assert second.read_bytes() == b"second-source-cleanup-failure"
    assert temp_evidence.read_bytes() == b"source-candidate-failure-temp-evidence"


def test_source_root_close_failure_preserves_confirmed_counts_and_stops_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, layout = _stack(tmp_path)
    orphan_payload = b"confirmed-source-cleanup-before-root-close-failure"
    orphan = layout.originals / f"{ORPHAN_ID}.bin"
    temp_evidence = layout.temp / f"{TEMP_STAGE_ID}.tmp"
    orphan.write_bytes(orphan_payload)
    temp_evidence.write_bytes(b"source-root-close-temp-evidence")
    original_close = WindowsHandle.close
    failures = 0

    def fail_source_root_close(handle: WindowsHandle) -> None:
        nonlocal failures
        is_source_root = handle.info.final_path.name == "originals"
        original_close(handle)
        if is_source_root and failures == 0:
            failures += 1
            raise RuntimeError("synthetic source root close failure")

    monkeypatch.setattr(WindowsHandle, "close", fail_source_root_close)

    result = build_windows_startup_recovery_service(manager).recover_startup()

    case = result.cases[0]
    assert failures == 1
    assert case.issues == (
        RecoveryIssueCode.CLEANUP_FAILED,
        RecoveryIssueCode.SOURCE_ARTIFACTS_RETAINED,
    )
    assert case.removed_source_orphans == 1
    assert case.released_bytes == len(orphan_payload)
    assert not orphan.exists()
    assert temp_evidence.read_bytes() == b"source-root-close-temp-evidence"


def test_source_root_close_failure_preserves_known_retained_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, layout = _stack(tmp_path)
    retained = layout.originals / "unrecognized-source-artifact.partial"
    retained.write_bytes(b"retained-source-artifact")
    original_close = WindowsHandle.close
    failures = 0

    def fail_source_root_close(handle: WindowsHandle) -> None:
        nonlocal failures
        is_source_root = handle.info.final_path.name == "originals"
        original_close(handle)
        if is_source_root and failures == 0:
            failures += 1
            raise RuntimeError("synthetic retained-inventory root close failure")

    monkeypatch.setattr(WindowsHandle, "close", fail_source_root_close)

    result = build_windows_startup_recovery_service(manager).recover_startup()

    case = result.cases[0]
    assert failures == 1
    assert case.issues == (
        RecoveryIssueCode.CLEANUP_FAILED,
        RecoveryIssueCode.SOURCE_ARTIFACTS_RETAINED,
    )
    assert case.removed_source_orphans == 0
    assert case.retained_source_artifacts == 1
    assert retained.read_bytes() == b"retained-source-artifact"


def test_thumbnail_root_close_failure_preserves_counts_and_stops_temp_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, layout = _stack(tmp_path)
    thumbnail_payload = b"confirmed-thumbnail-cleanup-before-root-close-failure"
    thumbnail_stage = layout.thumbnails / f"{THUMBNAIL_STAGE_ID}.tmp"
    temp_evidence = layout.temp / f"{TEMP_STAGE_ID}.tmp"
    thumbnail_stage.write_bytes(thumbnail_payload)
    temp_evidence.write_bytes(b"thumbnail-root-close-temp-evidence")
    original_close = WindowsHandle.close
    failures = 0

    def fail_thumbnail_root_close(handle: WindowsHandle) -> None:
        nonlocal failures
        is_thumbnail_root = handle.info.final_path.name == "thumbnails"
        original_close(handle)
        if is_thumbnail_root and failures == 0:
            failures += 1
            raise RuntimeError("synthetic thumbnail root close failure")

    monkeypatch.setattr(WindowsHandle, "close", fail_thumbnail_root_close)

    result = build_windows_startup_recovery_service(manager).recover_startup()

    case = result.cases[0]
    assert failures == 1
    assert case.issues == (
        RecoveryIssueCode.CLEANUP_FAILED,
        RecoveryIssueCode.THUMBNAIL_CLEANUP_PARTIAL,
    )
    assert case.removed_thumbnail_stages == 1
    assert case.released_bytes == len(thumbnail_payload)
    assert not thumbnail_stage.exists()
    assert temp_evidence.read_bytes() == b"thumbnail-root-close-temp-evidence"


def test_outer_transaction_finalizer_preserves_all_confirmed_cleanup_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, layout = _stack(tmp_path)
    orphan_payload = b"source-cleanup-before-transaction-finalizer-failure"
    temp_payload = b"temp-cleanup-before-transaction-finalizer-failure"
    orphan = layout.originals / f"{ORPHAN_ID}.bin"
    temp_stage = layout.temp / f"{TEMP_STAGE_ID}.tmp"
    orphan.write_bytes(orphan_payload)
    temp_stage.write_bytes(temp_payload)
    snapshot = WindowsManifestRepository(manager).load(CASE_ID).snapshot
    store = WindowsStartupRecoveryStore(
        manager,
        settle_exports=lambda _case_id: ExportHistoryRecoveryResult(),
    )
    original_guard = AuthenticatedCase.transaction_guard
    depth = 0
    failures = 0

    @contextmanager
    def fail_outer_transaction_finalizer(
        authority: AuthenticatedCase,
        *,
        coordination_checkpoint: Callable[[], None] | None = None,
    ) -> Iterator[None]:
        nonlocal depth, failures
        depth += 1
        outermost = depth == 1
        try:
            with original_guard(
                authority,
                coordination_checkpoint=coordination_checkpoint,
            ):
                yield
        finally:
            depth -= 1
            if outermost and failures == 0:
                failures += 1
                raise RuntimeError("synthetic outer transaction finalizer failure")

    monkeypatch.setattr(
        AuthenticatedCase,
        "transaction_guard",
        fail_outer_transaction_finalizer,
    )

    report = store._cleanup_loaded_case(snapshot)

    assert failures == 1
    assert report.issues == (RecoveryIssueCode.CLEANUP_FAILED,)
    assert report.source.removed_entries == 1
    assert report.source.released_bytes == len(orphan_payload)
    assert report.temp_files_removed == 1
    assert report.temp_released_bytes == len(temp_payload)
    assert not orphan.exists()
    assert not temp_stage.exists()


def test_concurrent_manifest_advance_reloads_before_cleanup(tmp_path: Path) -> None:
    manager, layout = _stack(tmp_path)
    orphan = layout.originals / f"{ORPHAN_ID}.bin"
    orphan.write_bytes(b"concurrent-manifest-source-residue")
    repository = WindowsManifestRepository(manager)
    observed: list[UUID] = []

    def advance_manifest(case_id: UUID) -> ExportHistoryRecoveryResult:
        opened = repository.load(case_id).snapshot
        repository.save(
            case_id,
            _next_manifest(opened.document),
            expected_revision=opened.revision,
            expected_sha256=opened.sha256,
            expected_cas_token=opened.cas_token,
        )
        observed.append(case_id)
        return ExportHistoryRecoveryResult()

    result = WindowsStartupRecoveryStore(
        manager,
        settle_exports=advance_manifest,
    ).scan_and_recover()

    assert observed == [CASE_ID]
    case = result.cases[0]
    assert case.status is CaseRecoveryStatus.CURRENT
    assert case.manifest_revision == 1
    assert case.issues == ()
    assert case.removed_source_orphans == 1
    assert not orphan.exists()


def test_repeated_manifest_change_retains_cleanup_candidate_for_attention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, layout = _stack(tmp_path)
    orphan = layout.originals / f"{ORPHAN_ID}.bin"
    orphan.write_bytes(b"repeated-manifest-change-source-residue")
    checks = 0

    def reject_stale_authority(*_args: object, **_kwargs: object) -> None:
        nonlocal checks
        checks += 1
        raise recovery_module._ManifestChanged

    monkeypatch.setattr(recovery_module, "_require_current_snapshot", reject_stale_authority)

    result = build_windows_startup_recovery_service(manager).recover_startup()

    assert checks == 2
    case = result.cases[0]
    assert case.status is CaseRecoveryStatus.CURRENT
    assert case.issues == (RecoveryIssueCode.CASE_CHANGED,)
    assert case.removed_source_orphans == 0
    assert orphan.read_bytes() == b"repeated-manifest-change-source-residue"


def test_corrupt_thumbnail_cache_is_lazily_removed_after_restart(tmp_path: Path) -> None:
    manager, layout = _stack(tmp_path)
    key = PdfThumbnailCacheKey(
        case_id=CASE_ID,
        source_id=SOURCE_ID,
        page_id=PAGE_ID,
        source_page_index=0,
        source_byte_size=123,
        source_sha256=hashlib.sha256(b"synthetic-source").hexdigest(),
        maximum_width_pixels=64,
        maximum_height_pixels=64,
        additional_rotation=PdfRenderRotation.DEGREES_0,
        renderer_profile_sha256=hashlib.sha256(b"synthetic-profile").hexdigest(),
    )
    corrupt = layout.thumbnails / key.internal_name
    corrupt.write_bytes(b"synthetic-corrupt-cache")
    manifest_before = layout.manifest.read_bytes()

    startup = build_windows_startup_recovery_service(manager).recover_startup()
    restarted_cache = WindowsThumbnailCacheStore(manager).lookup(key)

    assert startup.cases[0].status is CaseRecoveryStatus.CURRENT
    assert restarted_cache.status is ThumbnailCacheLookupStatus.CORRUPT_REMOVED
    assert not corrupt.exists()
    assert layout.manifest.read_bytes() == manifest_before


def test_capacity_failure_during_manifest_restore_preserves_all_recovery_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, layout = _stack(tmp_path)
    _publish_recoverable_checkpoint(manager, layout)
    orphan = layout.originals / f"{ORPHAN_ID}.bin"
    orphan.write_bytes(b"capacity-failure-source-residue")
    before = _tree_snapshot(layout.case_root)

    def reject_capacity(*_args: object, **_kwargs: object) -> None:
        reject(
            WorkspaceErrorCode.QUOTA_EXCEEDED,
            "quota.capacity",
            "Synthetic configured-capacity failure.",
        )

    monkeypatch.setattr(manager, "reserve_capacity", reject_capacity)

    result = build_windows_startup_recovery_service(manager).recover_startup()

    assert result.unavailable_cases == 1
    assert result.cases[0].issues == (RecoveryIssueCode.MANIFEST_RECOVERY_FAILED,)
    assert _tree_snapshot(layout.case_root) == before

"""Windows integration evidence for PWS-0112 export-history persistence."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from paperworkshell.app.export_history import PdfExportHistoryService
from paperworkshell.domain.manifest_repository import ManifestLoadResult, ManifestSaveResult
from paperworkshell.domain.pdf_export import (
    PdfExportOutcome,
    PdfExportOutcomeStatus,
    PdfExportPage,
    PdfExportPlan,
    PdfExportRotation,
    PdfExportSource,
    PdfExportSourceSignatureState,
    PdfExportVerificationReport,
)
from paperworkshell.domain.pdf_export_history import (
    PdfExportHistoryAppendStatus,
    PdfExportHistoryDocument,
    PdfExportHistoryError,
    PdfExportHistoryErrorCode,
    PdfExportHistoryRecord,
    build_pdf_export_history_record_for_commit,
    build_pdf_export_history_retry_receipt,
    canonical_pdf_export_history_bytes,
)
from paperworkshell.storage._windows import (
    WindowsHandle,
    rename_open_file_no_replace_at,
)
from paperworkshell.storage._windows import (
    open_handle as open_windows_handle,
)
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.errors import WorkspaceError, WorkspaceErrorCode
from paperworkshell.storage.export_history import (
    PdfExportHistoryPhase,
    PdfExportHistoryStorageDependencies,
    WindowsPdfExportHistoryRepository,
)
from paperworkshell.storage.manifest_repository import WindowsManifestRepository
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.workspace import CaseWorkspaceManager, CaseWorkspacePaths

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="PWS-0112 export history targets the supported Windows boundary"
)

CASE_ID = UUID("10000000-0000-4000-8000-000000000712")
SOURCE_ID = UUID("20000000-0000-4000-8000-000000000712")
PAGE_ID = UUID("30000000-0000-4000-8000-000000000712")
EXPORT_IDS = (
    UUID("50000000-0000-4000-8000-000000000712"),
    UUID("50000000-0000-4000-8000-000000000713"),
    UUID("50000000-0000-4000-8000-000000000714"),
)
CREATED_AT = datetime(2026, 8, 6, 8, 0, tzinfo=UTC)


def _quota(*, maximum_case_bytes: int = 16_000_000) -> WorkspaceQuota:
    return WorkspaceQuota(
        maximum_case_bytes=maximum_case_bytes,
        maximum_cache_bytes=min(maximum_case_bytes, 1_000_000),
        maximum_temp_bytes=min(maximum_case_bytes, 8_000_000),
        maximum_entries=256,
        maximum_depth=16,
    )


def _stack(tmp_path: Path) -> tuple[CaseWorkspaceManager, CaseWorkspacePaths]:
    profile = tmp_path / "profile"
    profile.mkdir()
    manager = CaseWorkspaceManager(
        profile_root=profile,
        quota=_quota(),
        id_factory=lambda: CASE_ID,
    )
    lease = WindowsCaseLifecycleStore(manager, clock=lambda: CREATED_AT).create_case()
    lease.close()
    return manager, manager.layout(CASE_ID)


def _record(
    manifests: WindowsManifestRepository,
    *,
    export_id: UUID = EXPORT_IDS[0],
    recorded_at: datetime = CREATED_AT + timedelta(hours=1),
) -> tuple[PdfExportPlan, PdfExportHistoryRecord, PdfExportOutcome]:
    manifest = manifests.load(CASE_ID).snapshot
    source = PdfExportSource(
        source_id=SOURCE_ID,
        byte_size=99,
        page_count=1,
        signature_state=PdfExportSourceSignatureState.NONE_DETECTED,
        sha256="a" * 64,
    )
    plan = PdfExportPlan(
        case_id=CASE_ID,
        manifest_revision=manifest.revision,
        manifest_sha256=manifest.sha256,
        manifest_cas_token=manifest.cas_token,
        sources=(source,),
        pages=(
            PdfExportPage(
                output_page_index=0,
                page_id=PAGE_ID,
                source_id=SOURCE_ID,
                source_page_index=0,
                additional_rotation=PdfExportRotation.DEGREES_0,
            ),
        ),
    )
    report = PdfExportVerificationReport.for_verified_plan(
        plan,
        output_sha256=hashlib.sha256(export_id.bytes).hexdigest(),
        output_byte_size=1234,
    )
    record = build_pdf_export_history_record_for_commit(
        plan,
        export_id=export_id,
        report=report,
        recorded_at=recorded_at,
    )
    receipt = build_pdf_export_history_retry_receipt(plan, record, report)
    outcome = PdfExportOutcome(
        export_id=export_id,
        status=PdfExportOutcomeStatus.COMMITTED_HISTORY_PENDING,
        report=report,
        destination_reopened=True,
        history_retry=receipt,
    )
    return plan, record, outcome


def test_record_first_manifest_link_reload_replace_and_exact_retry(tmp_path: Path) -> None:
    manager, layout = _stack(tmp_path)
    manifests = WindowsManifestRepository(manager)
    history = WindowsPdfExportHistoryRepository(manager)
    service = PdfExportHistoryService(history, manifests)
    first_plan, first_record, first_outcome = _record(manifests)

    first = service.record_and_link(first_plan, first_record, first_outcome)

    assert first.status is PdfExportOutcomeStatus.COMMITTED_RECORDED
    assert layout.export_history.exists()
    first_identity = os.stat(layout.export_history).st_ino
    loaded = history.load(CASE_ID)
    assert loaded.document.records == (first_record,)
    assert layout.export_history.read_bytes() == canonical_pdf_export_history_bytes(loaded.document)
    manifest_entries = manifests.load(CASE_ID).snapshot.document["export_history"]
    assert type(manifest_entries) is dict
    assert manifest_entries["entries"] == [first_record.manifest_reference]

    retry = history.append(CASE_ID, first_record)
    assert retry.status is PdfExportHistoryAppendStatus.ALREADY_PRESENT
    assert os.stat(layout.export_history).st_ino == first_identity

    second_plan, second_record, second_outcome = _record(
        manifests,
        export_id=EXPORT_IDS[1],
        recorded_at=CREATED_AT + timedelta(hours=2),
    )
    second = service.record_and_link(second_plan, second_record, second_outcome)
    assert second.status is PdfExportOutcomeStatus.COMMITTED_RECORDED
    assert os.stat(layout.export_history).st_ino != first_identity
    assert tuple(layout.temp.iterdir()) == ()
    fresh_manager = CaseWorkspaceManager(profile_root=manager.profile_root, quota=_quota())
    fresh_history = WindowsPdfExportHistoryRepository(fresh_manager).load(CASE_ID)
    assert fresh_history.document.records == (first_record, second_record)
    fresh_manifest = WindowsManifestRepository(fresh_manager).load(CASE_ID).snapshot.document
    history_ref = fresh_manifest["export_history"]
    assert type(history_ref) is dict
    assert history_ref["entries"] == [
        first_record.manifest_reference,
        second_record.manifest_reference,
    ]


def test_interruption_after_history_publish_leaves_retryable_orphan(tmp_path: Path) -> None:
    manager, layout = _stack(tmp_path)
    manifests = WindowsManifestRepository(manager)
    _plan, record, _outcome = _record(manifests)

    def interrupt(phase: PdfExportHistoryPhase) -> None:
        if phase is PdfExportHistoryPhase.AFTER_PUBLISH:
            raise KeyboardInterrupt

    interrupted = WindowsPdfExportHistoryRepository(
        manager,
        dependencies=PdfExportHistoryStorageDependencies(checkpoint=interrupt),
    )
    with pytest.raises(KeyboardInterrupt):
        interrupted.append(CASE_ID, record)

    assert layout.export_history.exists()
    assert WindowsPdfExportHistoryRepository(manager).load(CASE_ID).document.records == (record,)
    assert manifests.load(CASE_ID).snapshot.document["export_history"] == {
        "storage_name": "export-history.json",
        "entries": [],
    }
    retried = WindowsPdfExportHistoryRepository(manager).append(CASE_ID, record)
    assert retried.status is PdfExportHistoryAppendStatus.ALREADY_PRESENT


def test_replacement_interruption_after_publish_preserves_current_and_retired_bytes(
    tmp_path: Path,
) -> None:
    manager, layout = _stack(tmp_path)
    manifests = WindowsManifestRepository(manager)
    _plan, first, _outcome = _record(manifests)
    WindowsPdfExportHistoryRepository(manager).append(CASE_ID, first)
    _plan, second, _outcome = _record(manifests, export_id=EXPORT_IDS[1])

    def interrupt(phase: PdfExportHistoryPhase) -> None:
        if phase is PdfExportHistoryPhase.AFTER_PUBLISH:
            raise KeyboardInterrupt

    interrupted = WindowsPdfExportHistoryRepository(
        manager,
        dependencies=PdfExportHistoryStorageDependencies(checkpoint=interrupt),
    )
    with pytest.raises(KeyboardInterrupt):
        interrupted.append(CASE_ID, second)

    assert WindowsPdfExportHistoryRepository(manager).load(CASE_ID).document.records == (
        first,
        second,
    )
    retired = tuple(layout.temp.glob("*.retired"))
    assert len(retired) == 1
    assert retired[0].read_bytes() == canonical_pdf_export_history_bytes(
        PdfExportHistoryDocument((first,))
    )
    retried = WindowsPdfExportHistoryRepository(manager).append(CASE_ID, second)
    assert retried.status is PdfExportHistoryAppendStatus.ALREADY_PRESENT


def test_disk_full_during_record_replacement_preserves_last_good_history(tmp_path: Path) -> None:
    manager, layout = _stack(tmp_path)
    manifests = WindowsManifestRepository(manager)
    repository = WindowsPdfExportHistoryRepository(manager)
    _plan, first, _ = _record(manifests)
    repository.append(CASE_ID, first)
    history_before = layout.export_history.read_bytes()
    _plan, second, _ = _record(manifests, export_id=EXPORT_IDS[1])

    def disk_full(_path: Path, _payload: bytes) -> WindowsHandle:
        raise WorkspaceError(
            WorkspaceErrorCode.QUOTA_EXCEEDED,
            "quota.synthetic",
            "Synthetic disk-full boundary.",
        )

    failing = WindowsPdfExportHistoryRepository(
        manager,
        dependencies=PdfExportHistoryStorageDependencies(write_stage=disk_full),
    )
    with pytest.raises(PdfExportHistoryError) as raised:
        failing.append(CASE_ID, second)

    assert raised.value.code is PdfExportHistoryErrorCode.CAPACITY_EXCEEDED
    assert layout.export_history.read_bytes() == history_before
    assert WindowsPdfExportHistoryRepository(manager).load(CASE_ID).document.records == (first,)


def test_identity_race_is_uncertain_and_does_not_replace_unexpected_destination(
    tmp_path: Path,
) -> None:
    manager, layout = _stack(tmp_path)
    manifests = WindowsManifestRepository(manager)
    _plan, first, _ = _record(manifests)
    WindowsPdfExportHistoryRepository(manager).append(CASE_ID, first)
    _plan, second, _ = _record(manifests, export_id=EXPORT_IDS[1])
    raced_payload = canonical_pdf_export_history_bytes(
        WindowsPdfExportHistoryRepository(manager).load(CASE_ID).document
    )

    raced_once = False

    def race_before_publish(phase: PdfExportHistoryPhase) -> None:
        nonlocal raced_once
        if phase is not PdfExportHistoryPhase.BEFORE_PUBLISH or raced_once:
            return
        raced_once = True
        displaced = layout.export_history.with_name("displaced-history.bin")
        os.replace(layout.export_history, displaced)
        layout.export_history.write_bytes(raced_payload)

    raced = WindowsPdfExportHistoryRepository(
        manager,
        dependencies=PdfExportHistoryStorageDependencies(
            checkpoint=race_before_publish,
        ),
    )
    with pytest.raises(PdfExportHistoryError) as raised:
        raced.append(CASE_ID, second)

    assert raised.value.code is PdfExportHistoryErrorCode.COMMIT_UNCERTAIN
    assert layout.export_history.read_bytes() == raced_payload
    assert WindowsPdfExportHistoryRepository(manager).load(CASE_ID).document.records == (first,)


def test_race_inside_replacement_never_overwrites_unexpected_identity(tmp_path: Path) -> None:
    manager, layout = _stack(tmp_path)
    manifests = WindowsManifestRepository(manager)
    _plan, first, _ = _record(manifests)
    WindowsPdfExportHistoryRepository(manager).append(CASE_ID, first)
    _plan, second, _ = _record(manifests, export_id=EXPORT_IDS[1])
    _plan, rival, _ = _record(manifests, export_id=EXPORT_IDS[2])
    rival_payload = canonical_pdf_export_history_bytes(PdfExportHistoryDocument((first, rival)))
    raced = False

    def race_then_publish(
        source: WindowsHandle,
        destination_parent: WindowsHandle,
        destination_name: str,
    ) -> tuple[bool, int]:
        nonlocal raced
        if destination_name == "export-history.json" and not raced:
            assert not layout.export_history.exists()
            layout.export_history.write_bytes(rival_payload)
            raced = True
        return rename_open_file_no_replace_at(
            source,
            destination_parent,
            destination_name=destination_name,
        )

    repository = WindowsPdfExportHistoryRepository(
        manager,
        dependencies=PdfExportHistoryStorageDependencies(publish_new=race_then_publish),
    )
    with pytest.raises(PdfExportHistoryError) as raised:
        repository.append(CASE_ID, second)

    assert raced
    assert raised.value.code is PdfExportHistoryErrorCode.COMMIT_UNCERTAIN
    assert layout.export_history.read_bytes() == rival_payload
    assert WindowsPdfExportHistoryRepository(manager).load(CASE_ID).document.records == (
        first,
        rival,
    )


def test_stable_post_commit_open_blocks_destination_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, layout = _stack(tmp_path)
    manifests = WindowsManifestRepository(manager)
    _plan, first, _ = _record(manifests)
    WindowsPdfExportHistoryRepository(manager).append(CASE_ID, first)
    _plan, second, _ = _record(manifests, export_id=EXPORT_IDS[1])
    displaced = layout.export_history.with_name("displaced-after-open.bin")
    stable_reads = 0
    swap_blocked = False

    def race_after_open(path: Path, **kwargs: bool) -> WindowsHandle:
        nonlocal stable_reads, swap_blocked
        handle = open_windows_handle(path, **kwargs)
        if (
            Path(path) == layout.export_history
            and kwargs.get("read") is True
            and kwargs.get("delete", False) is False
            and kwargs.get("share_delete", False) is False
        ):
            stable_reads += 1
            if stable_reads == 2:
                try:
                    os.replace(layout.export_history, displaced)
                except PermissionError:
                    swap_blocked = True
        return handle

    monkeypatch.setattr(
        "paperworkshell.storage.export_history.open_handle",
        race_after_open,
    )
    result = WindowsPdfExportHistoryRepository(manager).append(CASE_ID, second)

    assert result.status is PdfExportHistoryAppendStatus.PUBLISHED
    assert swap_blocked
    assert not displaced.exists()
    assert WindowsPdfExportHistoryRepository(manager).load(CASE_ID).document.records == (
        first,
        second,
    )


def test_future_history_version_fails_before_mutation(tmp_path: Path) -> None:
    manager, layout = _stack(tmp_path)
    manifests = WindowsManifestRepository(manager)
    future = b'{"records":[],"schema_version":2}\n'
    layout.export_history.write_bytes(future)
    _plan, record, _ = _record(manifests)

    with pytest.raises(PdfExportHistoryError) as raised:
        WindowsPdfExportHistoryRepository(manager).append(CASE_ID, record)

    assert raised.value.code is PdfExportHistoryErrorCode.VERSION_UNSUPPORTED
    assert layout.export_history.read_bytes() == future


def test_partial_manifest_link_failure_keeps_record_for_fresh_process_retry(
    tmp_path: Path,
) -> None:
    manager, _layout = _stack(tmp_path)
    real_manifests = WindowsManifestRepository(manager)
    plan, record, outcome = _record(real_manifests)

    class _ManifestPort:
        def load(self, case_id: UUID) -> ManifestLoadResult:
            return real_manifests.load(case_id)

        def save(
            self,
            case_id: UUID,
            candidate: Mapping[str, object],
            *,
            expected_revision: int,
            expected_sha256: str,
            expected_cas_token: str,
        ) -> ManifestSaveResult:
            del (
                case_id,
                candidate,
                expected_revision,
                expected_sha256,
                expected_cas_token,
            )
            raise WorkspaceError(
                WorkspaceErrorCode.QUOTA_EXCEEDED,
                "quota.synthetic",
                "Synthetic manifest failure.",
            )

    history = WindowsPdfExportHistoryRepository(manager)
    pending = PdfExportHistoryService(history, _ManifestPort()).record_and_link(
        plan,
        record,
        outcome,
    )
    assert pending.status is PdfExportOutcomeStatus.COMMITTED_HISTORY_PENDING
    assert history.load(CASE_ID).document.records == (record,)

    fresh_manager = CaseWorkspaceManager(profile_root=manager.profile_root, quota=_quota())
    recorded = PdfExportHistoryService(
        WindowsPdfExportHistoryRepository(fresh_manager),
        WindowsManifestRepository(fresh_manager),
    ).retry_manifest_link(CASE_ID, record.export_id, record.sha256, pending)
    assert recorded.status is PdfExportOutcomeStatus.COMMITTED_RECORDED
    linked = WindowsManifestRepository(fresh_manager).load(CASE_ID).snapshot.document
    reference = linked["export_history"]
    assert type(reference) is dict
    assert reference["entries"] == [record.manifest_reference]

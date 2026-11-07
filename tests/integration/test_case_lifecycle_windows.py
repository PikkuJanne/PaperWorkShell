"""Real Windows acceptance evidence for the local case lifecycle."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from paperworkshell.app.case_lifecycle import CaseLifecycleService
from paperworkshell.domain.case_lifecycle import (
    CaseDeletionConfirmation,
    CaseDeletionStatus,
    CaseLifecycleError,
    CaseLifecycleErrorCode,
)
from paperworkshell.storage import case_lifecycle as lifecycle_storage
from paperworkshell.storage._windows import WindowsHandle
from paperworkshell.storage.case_lifecycle import (
    WindowsCaseLifecycleStore,
    build_initial_manifest_bytes,
)
from paperworkshell.storage.errors import WorkspaceError, WorkspaceErrorCode
from paperworkshell.storage.quotas import QuotaArea, WorkspaceQuota
from paperworkshell.storage.workspace import CaseWorkspaceManager

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="PWS-0102 targets the supported Windows lifecycle boundary"
)

FIRST_ID = UUID("10000000-0000-4000-8000-000000000101")
SECOND_ID = UUID("20000000-0000-4000-8000-000000000202")
MISMATCH_ID = UUID("30000000-0000-4000-8000-000000000303")
NOW = datetime(2026, 8, 2, 12, 34, 56, 123456, tzinfo=UTC)
CONFIRM = CaseDeletionConfirmation.REMOVE_FROM_APPLICATION_STORAGE


def _quota() -> WorkspaceQuota:
    return WorkspaceQuota(
        maximum_case_bytes=4_000_000,
        maximum_cache_bytes=1_000_000,
        maximum_temp_bytes=1_000_000,
        maximum_entries=256,
        maximum_depth=16,
    )


def _profile(tmp_path: Path) -> Path:
    profile = tmp_path / "profile"
    profile.mkdir()
    return profile


def _manager(
    profile: Path,
    *,
    identifiers: tuple[UUID, ...] = (),
) -> CaseWorkspaceManager:
    if identifiers:
        selected = iter(identifiers)
        return CaseWorkspaceManager(
            profile_root=profile,
            quota=_quota(),
            id_factory=lambda: next(selected),
        )
    return CaseWorkspaceManager(profile_root=profile, quota=_quota())


def _service(manager: CaseWorkspaceManager) -> CaseLifecycleService:
    return CaseLifecycleService(WindowsCaseLifecycleStore(manager, clock=lambda: NOW))


def test_named_case_creation_commits_private_name_and_reopens_by_stable_id(tmp_path: Path) -> None:
    manager = _manager(_profile(tmp_path), identifiers=(FIRST_ID,))
    service = _service(manager)
    created = service.create_case("Helsinki – hakemus äö")
    assert created.case_name == "Helsinki – hakemus äö"
    assert created.case_id == FIRST_ID
    assert manager.layout(FIRST_ID).case_root.name == str(FIRST_ID)
    service.close_case()
    reopened_service = _service(manager)
    reopened = reopened_service.open_case(FIRST_ID)
    assert reopened.case_name == created.case_name
    assert reopened.case_id == created.case_id
    assert service.list_cases().cases[0].case_name == created.case_name
    assert not list(manager.layout(FIRST_ID).temp.iterdir())
    reopened_service.close_case()


def _manifest_snapshot(path: Path) -> tuple[bytes, int, int, int]:
    stat = path.stat(follow_symlinks=False)
    return (path.read_bytes(), stat.st_ino, stat.st_size, stat.st_mtime_ns)


def _variant(kind: str, case_id: UUID) -> bytes:
    canonical = build_initial_manifest_bytes(case_id, NOW)
    if kind in {"future", "legacy"}:
        document = json.loads(canonical)
        document["schema_version"] = 5 if kind == "future" else 0
        return json.dumps(document, separators=(",", ":"), sort_keys=True).encode() + b"\n"
    if kind == "malformed":
        return b'{"schema_version":1'
    if kind == "duplicate-key":
        return b'{"schema_version":1,' + canonical.lstrip()[1:]
    if kind == "case-id-mismatch":
        return build_initial_manifest_bytes(MISMATCH_ID, NOW)
    raise AssertionError(f"unsupported test variant: {kind}")


def test_create_list_close_fresh_reopen_close_delete_round_trip(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    manager = _manager(profile, identifiers=(FIRST_ID,))
    service = _service(manager)

    created = service.create_case()
    layout = manager.layout(created.case_id)
    assert created.case_id == FIRST_ID
    assert service.list_cases().cases == (created,)
    assert service.list_cases().unavailable == ()

    service.close_case(FIRST_ID)
    before_reopen = _manifest_snapshot(layout.manifest)

    fresh_manager = _manager(profile)
    fresh_service = _service(fresh_manager)
    index = fresh_service.list_cases()
    reopened = fresh_service.open_case(FIRST_ID)

    assert index.cases == (created,)
    assert index.unavailable == ()
    assert reopened == created
    fresh_service.close_case(FIRST_ID)
    assert _manifest_snapshot(layout.manifest) == before_reopen

    result = fresh_service.delete_case(FIRST_ID, CONFIRM)

    assert result.status is CaseDeletionStatus.COMPLETE
    assert result.failure_code is None
    assert result.removed_files == 1
    assert result.removed_directories == 7
    assert result.released_bytes == len(before_reopen[0])
    assert "does not guarantee forensic erasure" in result.safe_message
    assert str(profile) not in repr(result)
    assert "securely erased" not in result.safe_message.casefold()
    assert not layout.case_root.exists()
    assert fresh_service.list_cases().cases == ()
    assert fresh_service.list_cases().unavailable == ()


def test_list_multiple_cases_is_deterministic_by_internal_id(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    manager = _manager(profile, identifiers=(SECOND_ID, FIRST_ID))
    service = _service(manager)

    second = service.create_case()
    service.close_case(second.case_id)
    first = service.create_case()
    service.close_case(first.case_id)

    index = _service(_manager(profile)).list_cases()

    assert second.case_id == SECOND_ID
    assert first.case_id == FIRST_ID
    assert tuple(summary.case_id for summary in index.cases) == (FIRST_ID, SECOND_ID)
    assert index.unavailable == ()


def test_invalid_uuid_named_candidate_does_not_hide_valid_cases(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    manager = _manager(profile, identifiers=(FIRST_ID,))
    service = _service(manager)
    first = service.create_case()
    service.close_case(FIRST_ID)
    invalid_candidate = manager.cases_root / str(SECOND_ID)
    invalid_candidate.write_bytes(b"not-a-case-directory")

    index = _service(_manager(profile)).list_cases()

    assert index.cases == (first,)
    assert tuple((issue.case_id, issue.code) for issue in index.unavailable) == (
        (SECOND_ID, CaseLifecycleErrorCode.MANIFEST_INVALID),
    )


def test_interrupted_lease_registration_rolls_back_coordination_state(
    tmp_path: Path,
) -> None:
    profile = _profile(tmp_path)
    manager = _manager(profile, identifiers=(FIRST_ID,))
    manager.provision()

    class InterruptingRegistry(dict[tuple[str, UUID], object]):
        def __setitem__(self, key: tuple[str, UUID], value: object) -> None:
            raise KeyboardInterrupt

    manager._reservation_states = InterruptingRegistry()  # type: ignore[assignment]
    with pytest.raises(KeyboardInterrupt):
        manager.authenticate_case(FIRST_ID, retain_lease=True)
    manager._reservation_states = {}

    with manager.authenticate_case(FIRST_ID, retain_lease=True):
        pass


def test_interrupted_open_summary_read_releases_retained_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile(tmp_path)
    manager = _manager(profile, identifiers=(FIRST_ID,))
    service = _service(manager)
    service.create_case()
    service.close_case(FIRST_ID)
    store = WindowsCaseLifecycleStore(manager, clock=lambda: NOW)

    def interrupt_summary_read(_authority: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(lifecycle_storage, "_read_case_summary", interrupt_summary_read)
    with pytest.raises(KeyboardInterrupt):
        store.open_case(FIRST_ID)

    result = store.delete_case(FIRST_ID, CONFIRM)
    assert result.status is CaseDeletionStatus.COMPLETE
    assert not manager.layout(FIRST_ID).case_root.exists()


def test_manifest_publication_supports_non_bmp_profile_components(tmp_path: Path) -> None:
    profile = tmp_path / "profile-\U0001f4c4"
    profile.mkdir()
    manager = _manager(profile, identifiers=(FIRST_ID,))
    service = _service(manager)

    created = service.create_case()
    service.close_case(created.case_id)
    deleted = service.delete_case(created.case_id, CONFIRM)

    assert created.case_id == FIRST_ID
    assert deleted.status is CaseDeletionStatus.COMPLETE


def test_missing_manifest_is_unavailable_and_cannot_be_opened(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    manager = _manager(profile)
    layout = manager.provision(FIRST_ID)
    service = _service(manager)

    index = service.list_cases()

    assert index.cases == ()
    assert tuple((issue.case_id, issue.code) for issue in index.unavailable) == (
        (FIRST_ID, CaseLifecycleErrorCode.CASE_INCOMPLETE),
    )
    with pytest.raises(CaseLifecycleError) as raised:
        service.open_case(FIRST_ID)
    assert raised.value.code is CaseLifecycleErrorCode.CASE_INCOMPLETE
    assert not layout.manifest.exists()


def test_interrupted_manifest_publication_never_becomes_valid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile(tmp_path)
    manager = _manager(profile, identifiers=(FIRST_ID,))
    service = _service(manager)

    def interrupt_publication(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(lifecycle_storage, "publish_new_file", interrupt_publication)

    with pytest.raises(KeyboardInterrupt):
        service.create_case()

    layout = manager.layout(FIRST_ID)
    index = service.list_cases()
    assert service.active_case is None
    assert layout.case_root.is_dir()
    assert not layout.manifest.exists()
    assert tuple(layout.temp.iterdir()) == ()
    assert index.cases == ()
    assert tuple((issue.case_id, issue.code) for issue in index.unavailable) == (
        (FIRST_ID, CaseLifecycleErrorCode.CASE_INCOMPLETE),
    )


@pytest.mark.parametrize(
    ("kind", "expected_code"),
    [
        ("future", CaseLifecycleErrorCode.MANIFEST_VERSION_UNSUPPORTED),
        ("legacy", CaseLifecycleErrorCode.MANIFEST_VERSION_UNSUPPORTED),
        ("malformed", CaseLifecycleErrorCode.MANIFEST_INVALID),
        ("duplicate-key", CaseLifecycleErrorCode.MANIFEST_INVALID),
        ("case-id-mismatch", CaseLifecycleErrorCode.MANIFEST_CASE_ID_MISMATCH),
    ],
)
def test_unsupported_or_invalid_manifest_open_and_delete_fail_before_mutation(
    tmp_path: Path,
    kind: str,
    expected_code: CaseLifecycleErrorCode,
) -> None:
    profile = _profile(tmp_path)
    manager = _manager(profile, identifiers=(FIRST_ID,))
    creator = _service(manager)
    creator.create_case()
    creator.close_case(FIRST_ID)
    layout = manager.layout(FIRST_ID)
    layout.manifest.write_bytes(_variant(kind, FIRST_ID))
    before = _manifest_snapshot(layout.manifest)
    service = _service(_manager(profile))

    with pytest.raises(CaseLifecycleError) as opened:
        service.open_case(FIRST_ID)
    assert opened.value.code is expected_code
    assert service.active_case is None
    assert _manifest_snapshot(layout.manifest) == before

    with pytest.raises(CaseLifecycleError) as deleted:
        service.delete_case(FIRST_ID, CONFIRM)
    assert deleted.value.code is expected_code
    assert _manifest_snapshot(layout.manifest) == before

    index = service.list_cases()
    assert index.cases == ()
    assert tuple((issue.case_id, issue.code) for issue in index.unavailable) == (
        (FIRST_ID, expected_code),
    )
    assert _manifest_snapshot(layout.manifest) == before


def test_retained_active_lease_blocks_other_service_deletion(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    first_manager = _manager(profile, identifiers=(FIRST_ID,))
    first_service = _service(first_manager)
    first_service.create_case()
    layout = first_manager.layout(FIRST_ID)
    before = _manifest_snapshot(layout.manifest)
    second_service = _service(_manager(profile))

    with pytest.raises(CaseLifecycleError) as raised:
        second_service.delete_case(FIRST_ID, CONFIRM)

    assert raised.value.code is CaseLifecycleErrorCode.DELETE_ACTIVE_CASE
    assert _manifest_snapshot(layout.manifest) == before
    first_service.close_case(FIRST_ID)

    result = second_service.delete_case(FIRST_ID, CONFIRM)
    assert result.status is CaseDeletionStatus.COMPLETE
    assert not layout.case_root.exists()


def test_active_capacity_reservation_blocks_deletion_before_manifest_mutation(
    tmp_path: Path,
) -> None:
    profile = _profile(tmp_path)
    manager = _manager(profile, identifiers=(FIRST_ID,))
    service = _service(manager)
    service.create_case()
    service.close_case(FIRST_ID)
    layout = manager.layout(FIRST_ID)
    before = _manifest_snapshot(layout.manifest)
    reservation = manager.reserve_capacity(
        FIRST_ID,
        QuotaArea.TEMP,
        relative_path="temp/reserved.tmp",
        is_directory=False,
        byte_count=1,
    )

    with pytest.raises(CaseLifecycleError) as raised:
        _service(_manager(profile)).delete_case(FIRST_ID, CONFIRM)

    assert raised.value.code is CaseLifecycleErrorCode.DELETE_ACTIVE_CASE
    assert _manifest_snapshot(layout.manifest) == before
    reservation.release()

    result = _service(_manager(profile)).delete_case(FIRST_ID, CONFIRM)
    assert result.status is CaseDeletionStatus.COMPLETE
    assert not layout.case_root.exists()


def test_manifest_changed_after_inventory_is_revalidated_before_first_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile(tmp_path)
    manager = _manager(profile, identifiers=(FIRST_ID,))
    service = _service(manager)
    service.create_case()
    service.close_case(FIRST_ID)
    layout = manager.layout(FIRST_ID)
    current = layout.manifest.read_bytes()
    future = current.replace(b'"schema_version":4', b'"schema_version":5', 1)
    assert future != current
    assert len(future) == len(current)
    real_inventory = manager._inventory_directory
    injected = False

    def replace_after_complete_inventory(*args: object, **kwargs: object) -> None:
        nonlocal injected
        real_inventory(*args, **kwargs)  # type: ignore[arg-type]
        if kwargs.get("top_level") is True and not injected:
            injected = True
            layout.manifest.write_bytes(future)

    monkeypatch.setattr(manager, "_inventory_directory", replace_after_complete_inventory)

    with pytest.raises(CaseLifecycleError) as raised:
        service.delete_case(FIRST_ID, CONFIRM)

    assert injected
    assert raised.value.code is CaseLifecycleErrorCode.MANIFEST_VERSION_UNSUPPORTED
    assert layout.manifest.read_bytes() == future
    assert layout.case_root.is_dir()


def test_failure_after_manifest_first_invalidation_is_partial_and_not_listed_valid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile(tmp_path)
    manager = _manager(profile, identifiers=(FIRST_ID,))
    service = _service(manager)
    service.create_case()
    service.close_case(FIRST_ID)
    layout = manager.layout(FIRST_ID)
    real_mark_for_deletion = WindowsHandle.mark_for_deletion
    calls = 0

    def fail_second_removal(handle: WindowsHandle) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise WorkspaceError(
                WorkspaceErrorCode.CLEANUP_FAILED,
                "test.delete",
                "Synthetic deletion interruption.",
            )
        real_mark_for_deletion(handle)

    monkeypatch.setattr(WindowsHandle, "mark_for_deletion", fail_second_removal)

    result = service.delete_case(FIRST_ID, CONFIRM)

    assert result.status is CaseDeletionStatus.PARTIAL
    assert result.failure_code is CaseLifecycleErrorCode.DELETE_FAILED
    assert result.removed_files == 1
    assert result.removed_directories == 0
    assert not layout.manifest.exists()
    assert layout.case_root.is_dir()
    assert "forensic erasure is not guaranteed" in result.safe_message
    index = service.list_cases()
    assert index.cases == ()
    assert tuple((issue.case_id, issue.code) for issue in index.unavailable) == (
        (FIRST_ID, CaseLifecycleErrorCode.CASE_INCOMPLETE),
    )

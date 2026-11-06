"""Real Windows consent-store atomicity, restart and case-scope evidence."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from paperworkshell.app.assisted_use import AssistedUseService
from paperworkshell.domain.assisted_use import (
    AssistedUseError,
    AssistedUseErrorCode,
    AssistedUseRecord,
    AssistedUseStatus,
    record_to_document,
)
from paperworkshell.domain.case_lifecycle import CaseDeletionConfirmation, CaseDeletionStatus
from paperworkshell.storage import assisted_use as assisted_storage
from paperworkshell.storage._windows import ManifestReplaceResult, ManifestReplaceState
from paperworkshell.storage.assisted_use import (
    ASSISTED_USE_RECORD_NAME,
    MAX_ASSISTED_USE_RECORD_BYTES,
    WindowsAssistedUseStore,
)
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.workspace import CaseWorkspaceManager

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows authenticated storage boundary")
CASE_ID = UUID("70000000-0000-4000-8000-000000000705")
OTHER_ID = UUID("71000000-0000-4000-8000-000000000705")
NOW = datetime(2026, 11, 2, 12, tzinfo=UTC)


def _record(case_id: UUID = CASE_ID) -> AssistedUseRecord:
    return AssistedUseRecord(case_id, NOW, NOW + timedelta(days=7), NOW, 7, bytes(16), bytes(32))


def _stack(tmp_path: Path) -> tuple[CaseWorkspaceManager, WindowsAssistedUseStore]:
    profile = tmp_path / "profile"
    profile.mkdir()
    manager = CaseWorkspaceManager(
        profile_root=profile,
        quota=WorkspaceQuota(4_000_000, 1_000_000, 1_000_000, 256, 16),
        id_factory=lambda: CASE_ID,
    )
    lease = WindowsCaseLifecycleStore(manager, clock=lambda: NOW).create_case()
    lease.close()
    return manager, WindowsAssistedUseStore(manager)


def _snapshot(path: Path) -> tuple[bytes, int, int]:
    stat = path.stat(follow_symlinks=False)
    return path.read_bytes(), stat.st_ino, stat.st_mtime_ns


def test_record_round_trip_and_monotonic_update_preserve_manifest(tmp_path: Path) -> None:
    manager, store = _stack(tmp_path)
    layout = manager.layout(CASE_ID)
    original = _snapshot(layout.manifest)
    assert store.load(CASE_ID) is None
    store.save(_record())
    assert WindowsAssistedUseStore(manager).load(CASE_ID) == _record()
    before = _snapshot(layout.journal / ASSISTED_USE_RECORD_NAME)
    store.save(_record())
    assert _snapshot(layout.journal / ASSISTED_USE_RECORD_NAME) == before
    updated = replace(_record(), last_seen_at=NOW + timedelta(minutes=1))
    store.save(updated)
    assert store.load(CASE_ID) == updated
    assert _snapshot(layout.manifest) == original
    assert tuple(layout.temp.iterdir()) == ()
    assert str(tmp_path) not in repr(store)
    assert store.load(CASE_ID) is not None


@pytest.mark.parametrize(
    "variant", ["old", "future", "malformed", "duplicate", "mismatch", "oversize", "nested"]
)
def test_invalid_policy_is_never_changed_or_treated_as_ordinary(
    tmp_path: Path, variant: str
) -> None:
    manager, store = _stack(tmp_path)
    path = manager.layout(CASE_ID).journal / ASSISTED_USE_RECORD_NAME
    document = record_to_document(_record())
    if variant in {"old", "future"}:
        document["schema_version"] = 0 if variant == "old" else 2
        payload = json.dumps(document).encode()
    elif variant == "mismatch":
        payload = json.dumps(record_to_document(_record(OTHER_ID))).encode()
    elif variant == "duplicate":
        payload = b'{"schema_version":1,' + json.dumps(document).encode()[1:]
    elif variant == "oversize":
        payload = b" " * (MAX_ASSISTED_USE_RECORD_BYTES + 1)
    elif variant == "nested":
        payload = b"[" * 1500 + b"0" + b"]" * 1500
    else:
        payload = b'{"broken":'
    path.write_bytes(payload)
    before = _snapshot(path)
    for saving in (False, True):
        with pytest.raises(AssistedUseError) as raised:
            if saving:
                store.save(_record())
            else:
                store.load(CASE_ID)
        assert str(tmp_path) not in str(raised.value)
        assert _snapshot(path) == before
    assert tuple(manager.layout(CASE_ID).temp.iterdir()) == ()


@pytest.mark.parametrize("variant", ["backwards", "credential", "retention", "expired"])
def test_stale_or_replaced_policy_cannot_weaken_existing_record(
    tmp_path: Path, variant: str
) -> None:
    manager, store = _stack(tmp_path)
    previous = replace(_record(), last_seen_at=NOW + timedelta(minutes=2))
    if variant == "expired":
        previous = replace(previous, expired=True)
    store.save(previous)
    path = manager.layout(CASE_ID).journal / ASSISTED_USE_RECORD_NAME
    before = _snapshot(path)
    candidate = replace(previous, last_seen_at=NOW + timedelta(minutes=3))
    if variant == "backwards":
        candidate = replace(candidate, last_seen_at=NOW)
    elif variant == "credential":
        candidate = replace(candidate, verifier=bytes([1]) * 32)
    elif variant == "retention":
        candidate = replace(candidate, retention_days=30, expires_at=NOW + timedelta(days=30))
    else:
        candidate = replace(candidate, expired=False)
    with pytest.raises(AssistedUseError) as raised:
        store.save(candidate)
    assert raised.value.code is AssistedUseErrorCode.INVALID
    assert _snapshot(path) == before


@pytest.mark.parametrize("existing", [False, True])
def test_publication_failure_preserves_previous_policy_and_cleans_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool
) -> None:
    manager, store = _stack(tmp_path)
    path = manager.layout(CASE_ID).journal / ASSISTED_USE_RECORD_NAME
    if existing:
        store.save(_record())
    before = _snapshot(path) if existing else None
    monkeypatch.setattr(
        assisted_storage,
        "publish_assisted_use_record_atomically",
        lambda *_args: ManifestReplaceResult(ManifestReplaceState.NOT_COMMITTED),
    )
    with pytest.raises(AssistedUseError) as raised:
        store.save(replace(_record(), last_seen_at=NOW + timedelta(minutes=1)))
    assert raised.value.code is AssistedUseErrorCode.STORAGE_FAILED
    if before is None:
        assert not path.exists()
    else:
        assert _snapshot(path) == before
    assert tuple(manager.layout(CASE_ID).temp.iterdir()) == ()


def test_disk_full_before_stage_write_is_sanitized_and_preserves_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, store = _stack(tmp_path)
    store.save(_record())
    path = manager.layout(CASE_ID).journal / ASSISTED_USE_RECORD_NAME
    before = _snapshot(path)

    def full(_path: Path, _payload: bytes) -> None:
        raise OSError(28, "SYNTHETIC_PRIVATE_FULL_PATH")

    monkeypatch.setattr(assisted_storage, "write_new_file", full)
    with pytest.raises(AssistedUseError) as raised:
        store.save(replace(_record(), last_seen_at=NOW + timedelta(minutes=1)))
    assert raised.value.code is AssistedUseErrorCode.STORAGE_FAILED
    assert "SYNTHETIC_PRIVATE" not in str(raised.value)
    assert _snapshot(path) == before
    assert tuple(manager.layout(CASE_ID).temp.iterdir()) == ()


def test_hardlinked_policy_rejected_without_altering_outside_file(tmp_path: Path) -> None:
    manager, store = _stack(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(record_to_document(_record())), encoding="utf-8")
    path = manager.layout(CASE_ID).journal / ASSISTED_USE_RECORD_NAME
    os.link(outside, path)
    before = _snapshot(outside)
    with pytest.raises(AssistedUseError):
        store.load(CASE_ID)
    with pytest.raises(AssistedUseError):
        store.save(_record())
    assert _snapshot(outside) == before


def test_case_delete_removes_policy_without_touching_sibling_or_exports(tmp_path: Path) -> None:
    manager, store = _stack(tmp_path)
    store.save(_record())
    other_manager = CaseWorkspaceManager(
        profile_root=tmp_path / "profile",
        quota=WorkspaceQuota(4_000_000, 1_000_000, 1_000_000, 256, 16),
        id_factory=lambda: OTHER_ID,
    )
    lease = WindowsCaseLifecycleStore(other_manager, clock=lambda: NOW).create_case()
    lease.close()
    WindowsAssistedUseStore(other_manager).save(_record(OTHER_ID))
    sibling = other_manager.layout(OTHER_ID).journal / ASSISTED_USE_RECORD_NAME
    outside = tmp_path / "export-sentinel.pdf"
    outside.write_bytes(b"synthetic-export")
    sibling_before, outside_before = _snapshot(sibling), _snapshot(outside)
    result = WindowsCaseLifecycleStore(manager).delete_case(
        CASE_ID, CaseDeletionConfirmation.REMOVE_FROM_APPLICATION_STORAGE
    )
    assert result.status is CaseDeletionStatus.COMPLETE
    assert not manager.layout(CASE_ID).case_root.exists()
    assert _snapshot(sibling) == sibling_before
    assert _snapshot(outside) == outside_before


def test_real_store_restart_and_clock_jumps_never_restore_access(tmp_path: Path) -> None:
    manager, store = _stack(tmp_path)
    wall = [NOW]
    tick = [100.0]
    original = _snapshot(manager.layout(CASE_ID).manifest)
    first = AssistedUseService(store, clock=lambda: wall[0], monotonic_clock=lambda: tick[0])
    phrase = "Synthetic assisted phrase"
    assert first.enroll(CASE_ID, 1, phrase, consent=True).status is AssistedUseStatus.UNLOCKED
    # Simulate process loss without releasing the in-memory lease. No unlocked
    # fact is persisted or inherited by a newly composed application service.
    restarted = AssistedUseService(
        WindowsAssistedUseStore(manager), clock=lambda: wall[0], monotonic_clock=lambda: tick[0]
    )
    assert restarted.inspect(CASE_ID).status is AssistedUseStatus.LOCKED
    with pytest.raises(AssistedUseError) as denied:
        restarted.require_access(CASE_ID)
    assert denied.value.code is AssistedUseErrorCode.LOCKED
    restarted.unlock(CASE_ID, phrase)
    wall[0] = NOW + timedelta(days=2)
    tick[0] += 1
    snapshot = restarted.check()
    assert snapshot is not None and snapshot.status is AssistedUseStatus.EXPIRED
    wall[0] = NOW
    final = AssistedUseService(
        WindowsAssistedUseStore(manager), clock=lambda: wall[0], monotonic_clock=lambda: tick[0]
    )
    assert final.inspect(CASE_ID).status is AssistedUseStatus.EXPIRED
    with pytest.raises(AssistedUseError) as expired:
        final.unlock(CASE_ID, phrase)
    assert expired.value.code is AssistedUseErrorCode.EXPIRED
    assert _snapshot(manager.layout(CASE_ID).manifest) == original


def test_low_disk_reservation_precheck_preserves_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, store = _stack(tmp_path)
    store.save(_record())
    path = manager.layout(CASE_ID).journal / ASSISTED_USE_RECORD_NAME
    before = _snapshot(path)

    def no_capacity(_manager: CaseWorkspaceManager, _case_root: Path, *, byte_count: int) -> None:
        assert byte_count > 0
        raise OSError(28, "SYNTHETIC_NO_CAPACITY")

    monkeypatch.setattr(CaseWorkspaceManager, "_require_volume_capacity", no_capacity)
    with pytest.raises(AssistedUseError) as rejected:
        store.save(replace(_record(), last_seen_at=NOW + timedelta(minutes=1)))
    assert rejected.value.code is AssistedUseErrorCode.STORAGE_FAILED
    assert _snapshot(path) == before
    assert tuple(manager.layout(CASE_ID).temp.iterdir()) == ()

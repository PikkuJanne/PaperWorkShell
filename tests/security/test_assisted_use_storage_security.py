"""Hostile path, scope and diagnostic checks for the assisted-use journal."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from paperworkshell.domain.assisted_use import AssistedUseError, AssistedUseRecord
from paperworkshell.storage import assisted_use as assisted_storage
from paperworkshell.storage._windows import WindowsHandle, write_new_file
from paperworkshell.storage.assisted_use import ASSISTED_USE_RECORD_NAME, WindowsAssistedUseStore
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.workspace import CaseWorkspaceManager

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows authenticated storage boundary")
CASE_ID = UUID("72000000-0000-4000-8000-000000000705")
NOW = datetime(2026, 11, 2, 12, tzinfo=UTC)


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


def _record() -> AssistedUseRecord:
    return AssistedUseRecord(CASE_ID, NOW, NOW + timedelta(days=1), NOW, 1, bytes(16), bytes(32))


@pytest.mark.parametrize("bad_id", ["../outside", "C:\\outside", str(CASE_ID), None, True])
def test_case_identity_never_becomes_a_path(tmp_path: Path, bad_id: object) -> None:
    manager, store = _stack(tmp_path)
    before = manager.layout(CASE_ID).manifest.read_bytes()
    with pytest.raises(AssistedUseError):
        store.load(bad_id)  # type: ignore[arg-type]
    assert manager.layout(CASE_ID).manifest.read_bytes() == before
    assert tuple(manager.layout(CASE_ID).journal.iterdir()) == ()


def test_publication_pins_temp_directory_before_creating_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, store = _stack(tmp_path)
    layout = manager.layout(CASE_ID)
    original_writer = write_new_file
    substitution_attempted = False

    def writer(path: Path, payload: bytes) -> WindowsHandle:
        nonlocal substitution_attempted
        substitution_attempted = True
        with pytest.raises(OSError):
            layout.temp.rename(layout.case_root / "substituted-temp")
        return original_writer(path, payload)

    monkeypatch.setattr(assisted_storage, "write_new_file", writer)
    store.save(_record())
    assert substitution_attempted
    assert store.load(CASE_ID) == _record()
    assert not (layout.case_root / "substituted-temp").exists()


def test_alternate_streams_rejected_without_changing_policy(tmp_path: Path) -> None:
    manager, store = _stack(tmp_path)
    store.save(_record())
    path = manager.layout(CASE_ID).journal / ASSISTED_USE_RECORD_NAME
    payload = path.read_bytes()
    stream = Path(str(path) + ":unexpected")
    stream.write_bytes(b"synthetic-hidden-value")
    with pytest.raises(AssistedUseError):
        store.load(CASE_ID)
    with pytest.raises(AssistedUseError):
        store.save(_record())
    assert path.read_bytes() == payload
    assert stream.read_bytes() == b"synthetic-hidden-value"


def test_store_does_not_log_paths_policy_or_credential_material(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    manager, store = _stack(tmp_path)
    with caplog.at_level("DEBUG"):
        store.save(_record())
        assert store.load(CASE_ID) == _record()
    path = manager.layout(CASE_ID).journal / ASSISTED_USE_RECORD_NAME
    assert caplog.text == ""
    assert str(path) not in repr(store)
    assert _record().salt.hex() not in repr(_record())
    assert _record().verifier.hex() not in repr(_record())

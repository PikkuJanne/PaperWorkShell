"""A failed native test must retain its journal and exact code together."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.conftest import OwnedWorkerCodeSnapshot, _owned_worker_recovery_is_empty


def test_numbered_pytest_cleanup_cannot_remove_durable_owned_code_journal(tmp_path: Path) -> None:
    numbered = tmp_path / "pytest-numbered" / "old-session"
    numbered.mkdir(parents=True)
    stage = tmp_path / "workspace" / "owned-stage"
    source = stage / "owned-production-code" / "src"
    source.mkdir(parents=True)
    snapshot = OwnedWorkerCodeSnapshot(source, "[]")
    journal = snapshot.recovery_root / "fixed-test-scope"
    journal.mkdir(parents=True)
    record = journal / "unresolved.json"
    record.write_bytes(b"synthetic retained record")
    assert not journal.is_relative_to(numbered)
    assert numbered.resolve().is_relative_to(tmp_path.resolve())
    assert numbered.resolve() != tmp_path.resolve()
    shutil.rmtree(numbered)
    assert record.read_bytes() == b"synthetic retained record"
    assert not _owned_worker_recovery_is_empty(snapshot.recovery_root)


def test_unknown_or_unreadable_recovery_members_refuse_source_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "recovery"
    root.mkdir()
    assert _owned_worker_recovery_is_empty(root)

    def refused(_path: object) -> object:
        raise PermissionError("synthetic unreadable recovery directory")

    monkeypatch.setattr("tests.conftest.os.scandir", refused)
    assert not _owned_worker_recovery_is_empty(root)


def test_excess_recovery_directory_inventory_is_preserved(tmp_path: Path) -> None:
    root = tmp_path / "recovery"
    root.mkdir()
    for number in range(257):
        (root / str(number)).mkdir()
    assert not _owned_worker_recovery_is_empty(root)
    assert len(tuple(root.iterdir())) == 257

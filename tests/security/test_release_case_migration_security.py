"""Migration refuses hostile/future state before writes and protects retained source bytes."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from paperworkshell.domain.manifest_repository import (
    ManifestRepositoryError,
    ManifestRepositoryErrorCode,
)
from paperworkshell.storage import manifest_migration_backup as backup_storage
from paperworkshell.storage._windows import (
    WindowsHandle,
    rename_open_file_no_replace_at,
    write_new_file,
)
from paperworkshell.storage.manifest_repository import WindowsManifestRepository
from tests.helpers.release_migration import CASE_ID, file_bytes, release_case

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Authenticated Windows storage required")


@pytest.mark.parametrize("target", ["manifest", "checkpoint"])
def test_future_dispatch_never_creates_backup_or_changes_any_case_file(
    tmp_path: Path, target: str
) -> None:
    manager, layout, source, _payload = release_case(tmp_path)
    source["schema_version"] = 5
    source["private_future_field"] = {"value": "PWS0804_PRIVATE_FUTURE_TOKEN"}
    path = layout.manifest if target == "manifest" else layout.journal / "latest.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    before = file_bytes(layout.case_root)
    with pytest.raises(ManifestRepositoryError) as raised:
        WindowsManifestRepository(manager).load(CASE_ID)
    assert raised.value.code is ManifestRepositoryErrorCode.VERSION_UNSUPPORTED
    assert file_bytes(layout.case_root) == before
    assert "PRIVATE" not in str(raised.value)


@pytest.mark.parametrize("payload", [b'{"schema_version":1', b"not a case record"])
def test_corrupt_case_fails_without_backup_or_new_checkpoint(
    tmp_path: Path, payload: bytes
) -> None:
    manager, layout, _source, _payload = release_case(tmp_path)
    layout.manifest.write_bytes(payload)
    before = file_bytes(layout.case_root)
    with pytest.raises(ManifestRepositoryError):
        WindowsManifestRepository(manager).load(CASE_ID)
    assert file_bytes(layout.case_root) == before


@pytest.mark.parametrize("kind", ["changed", "directory", "hardlink"])
def test_existing_unsafe_or_different_backup_is_never_replaced(tmp_path: Path, kind: str) -> None:
    manager, layout, _source, old_bytes = release_case(tmp_path)
    backup = layout.journal / "migration-source-v1.json"
    if kind == "changed":
        backup.write_bytes(b"PWS0804_PRIVATE_BACKUP_TOKEN")
    elif kind == "directory":
        backup.mkdir()
    else:
        outside = tmp_path / "external-synthetic-sentinel.bin"
        outside.write_bytes(old_bytes)
        os.link(outside, backup)
    before = file_bytes(layout.case_root)
    with pytest.raises(ManifestRepositoryError) as raised:
        WindowsManifestRepository(manager).load(CASE_ID)
    assert file_bytes(layout.case_root) == before
    assert layout.manifest.read_bytes() == old_bytes
    assert "PRIVATE" not in str(raised.value)
    assert not (layout.journal / "latest.json").exists()


def test_interruption_immediately_after_backup_rename_keeps_exact_recovery_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, layout, _source, old_bytes = release_case(tmp_path)

    def interrupt(
        source_handle: WindowsHandle,
        destination_parent: WindowsHandle,
        *,
        destination_name: str,
    ) -> tuple[bool, int]:
        rename_open_file_no_replace_at(
            source_handle, destination_parent, destination_name=destination_name
        )
        raise KeyboardInterrupt

    monkeypatch.setattr(backup_storage, "rename_open_file_no_replace_at", interrupt)
    with pytest.raises(KeyboardInterrupt):
        WindowsManifestRepository(manager).load(CASE_ID)
    assert (layout.journal / "migration-source-v1.json").read_bytes() == old_bytes
    assert layout.manifest.read_bytes() == old_bytes
    assert not (layout.journal / "latest.json").exists()
    assert not list(layout.temp.iterdir())
    monkeypatch.undo()
    assert WindowsManifestRepository(manager).load(CASE_ID).snapshot.schema_version == 4


def test_migration_produces_no_content_bearing_routine_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    manager, layout, source, _old_bytes = release_case(tmp_path)
    sources = source["sources"]
    assert type(sources) is list
    source_record = sources[0]
    assert type(source_record) is dict
    source_record["display_name"] = "PWS0804_PRIVATE_FILENAME.pdf"
    layout.manifest.write_text(json.dumps(source), encoding="utf-8")
    WindowsManifestRepository(manager).load(CASE_ID)
    captured = capsys.readouterr()
    assert "PRIVATE_FILENAME" not in caplog.text + captured.out + captured.err


@pytest.mark.parametrize("target", ["case", "journal", "temp"])
def test_migration_directory_rename_is_blocked_before_backup_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """A retained journal handle must never publish source bytes outside its case."""

    manager, layout, _source, old_bytes = release_case(tmp_path)
    selected = {"case": layout.case_root, "journal": layout.journal, "temp": layout.temp}[target]
    outside = tmp_path / "synthetic-escaped-directory"
    original_writer = write_new_file
    attempted = False
    moved = False

    def race(path: Path, payload: bytes) -> WindowsHandle:
        nonlocal attempted, moved
        attempted = True
        try:
            selected.rename(outside)
            moved = True
        except OSError:
            pass
        return original_writer(path, payload)

    monkeypatch.setattr(backup_storage, "write_new_file", race)
    result = None
    try:
        result = WindowsManifestRepository(manager).load(CASE_ID)
    except ManifestRepositoryError:
        pass
    assert attempted
    escaped_bytes = (
        tuple(path.read_bytes() for path in outside.rglob("*") if path.is_file())
        if outside.exists()
        else ()
    )
    assert old_bytes not in escaped_bytes, "source manifest bytes escaped the authenticated case"
    assert not moved, "a migration directory escaped the authenticated case"
    assert not outside.exists()
    assert result is not None and result.snapshot.schema_version == 4
    assert (layout.journal / "migration-source-v1.json").read_bytes() == old_bytes
    assert not list(layout.temp.iterdir())

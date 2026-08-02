"""Windows evidence for the exact-handle primitives introduced by PWS-0104."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest

import paperworkshell.storage._windows as windows_module
from paperworkshell.storage._windows import (
    ManifestReplaceState,
    create_pending_source_file,
    open_handle,
    publish_new_original,
    replace_manifest_atomically,
    require_expected_handle,
    write_new_file,
)
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.errors import WorkspaceError, WorkspaceErrorCode
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.workspace import CaseWorkspaceManager, CaseWorkspacePaths

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="PWS-0104 targets the supported Windows storage boundary"
)

SOURCE_ID = UUID("40000000-0000-4000-8000-000000000404")


def _quota() -> WorkspaceQuota:
    return WorkspaceQuota(
        maximum_case_bytes=8_000_000,
        maximum_cache_bytes=1_000_000,
        maximum_temp_bytes=4_000_000,
        maximum_entries=256,
        maximum_depth=16,
    )


def _case(tmp_path: Path) -> tuple[CaseWorkspaceManager, CaseWorkspacePaths]:
    profile = tmp_path / "profile"
    profile.mkdir()
    manager = CaseWorkspaceManager(profile_root=profile, quota=_quota())
    lease = WindowsCaseLifecycleStore(manager).create_case()
    layout = manager.layout(lease.summary.case_id)
    lease.close()
    return manager, layout


def _candidate_bytes(current: bytes) -> bytes:
    return current.replace(b'"revision":0', b'"revision":1', 1)


def _cleanup_file_handle(handle: windows_module.WindowsHandle) -> None:
    try:
        handle.mark_for_deletion()
    finally:
        handle.close()


def test_pending_source_streams_flushes_rehashes_and_publishes_once(
    tmp_path: Path,
) -> None:
    _manager, layout = _case(tmp_path)
    payload = b"A" * (1024 * 1024) + b"bounded-tail"
    stage_path = layout.temp / f"{uuid4()}.tmp"
    storage_name = f"{SOURCE_ID}.bin"

    with open_handle(layout.temp) as temp_root:
        require_expected_handle(temp_root, layout.temp, directory=True)
        stage = create_pending_source_file(stage_path, temp_root=temp_root)
        try:
            stage.write_chunk(payload[: 1024 * 1024])
            stage.write_chunk(payload[1024 * 1024 :])
            sealed = stage.seal(expected_size=len(payload))
            assert sealed.logical_size == len(payload)

            with pytest.raises(OSError):
                stage_path.write_bytes(b"replacement")
            with pytest.raises(OSError):
                stage_path.unlink()

            stage.rewind()
            copied = stage.read_exact(1024 * 1024) + stage.read_exact(len(b"bounded-tail"))
            stage.require_eof()
            assert hashlib.sha256(copied).digest() == hashlib.sha256(payload).digest()

            with open_handle(layout.originals) as originals_root:
                published = publish_new_original(
                    stage,
                    originals_root,
                    storage_name=storage_name,
                )
            assert published.identity == sealed.identity
            assert published.final_path == layout.originals / storage_name
        finally:
            stage.close()

    original = layout.originals / storage_name
    assert original.read_bytes() == payload
    assert not stage_path.exists()

    second_stage_path = layout.temp / f"{uuid4()}.tmp"
    with open_handle(layout.temp) as temp_root:
        second = create_pending_source_file(second_stage_path, temp_root=temp_root)
        try:
            second.write_chunk(b"different")
            second.seal(expected_size=9)
            with open_handle(layout.originals) as originals_root:
                with pytest.raises(WorkspaceError) as raised:
                    publish_new_original(
                        second,
                        originals_root,
                        storage_name=storage_name,
                    )
            assert raised.value.code is WorkspaceErrorCode.ALREADY_EXISTS
            assert original.read_bytes() == payload
        finally:
            _cleanup_file_handle(second.handle)


def test_ordinary_read_handle_supports_bounded_integrity_rehash_and_pins_name(
    tmp_path: Path,
) -> None:
    _manager, layout = _case(tmp_path)
    path = layout.originals / f"{SOURCE_ID}.bin"
    payload = b"immutable-case-copy"
    path.write_bytes(payload)

    with open_handle(path, read=True) as handle:
        require_expected_handle(handle, path, directory=False)
        handle.rewind()
        assert handle.read_exact(7) + handle.read_exact(len(payload) - 7) == payload
        refreshed = handle.require_eof()
        assert refreshed.logical_size == len(payload)
        with pytest.raises(OSError):
            path.write_bytes(b"changed")
        with pytest.raises(OSError):
            path.unlink()

    path.write_bytes(b"changed")
    assert path.read_bytes() == b"changed"


def test_manifest_replace_reports_committed_by_candidate_identity(tmp_path: Path) -> None:
    manager, layout = _case(tmp_path)
    previous = layout.manifest.read_bytes()
    candidate_payload = _candidate_bytes(previous)
    candidate_path = layout.temp / f"{uuid4()}.tmp"
    candidate = write_new_file(candidate_path, candidate_payload)
    try:
        with manager.authenticate_case(layout.case_id) as authority:
            with open_handle(
                layout.manifest,
                read=True,
                delete=True,
                share_delete=True,
            ) as expected:
                result = replace_manifest_atomically(candidate, authority.handle, expected)
        assert result.state is ManifestReplaceState.COMMITTED
        assert candidate.info.final_path == layout.manifest
        candidate.close()
        assert layout.manifest.read_bytes() == candidate_payload
        assert not candidate_path.exists()
    finally:
        candidate.close()


def test_manifest_replace_without_target_delete_sharing_is_not_committed(
    tmp_path: Path,
) -> None:
    manager, layout = _case(tmp_path)
    previous = layout.manifest.read_bytes()
    candidate_path = layout.temp / f"{uuid4()}.tmp"
    candidate = write_new_file(candidate_path, _candidate_bytes(previous))
    try:
        with manager.authenticate_case(layout.case_id) as authority:
            with open_handle(layout.manifest, read=True, delete=True) as expected:
                result = replace_manifest_atomically(candidate, authority.handle, expected)
        assert result.state is ManifestReplaceState.NOT_COMMITTED
        assert layout.manifest.read_bytes() == previous
        assert candidate_path.exists()
    finally:
        _cleanup_file_handle(candidate)


def test_manifest_replace_rejects_candidate_from_another_case(tmp_path: Path) -> None:
    manager, layout = _case(tmp_path)
    other_lease = WindowsCaseLifecycleStore(manager).create_case()
    other_layout = manager.layout(other_lease.summary.case_id)
    other_lease.close()
    previous = layout.manifest.read_bytes()
    candidate_path = other_layout.temp / f"{uuid4()}.tmp"
    candidate = write_new_file(candidate_path, _candidate_bytes(previous))
    try:
        with manager.authenticate_case(layout.case_id) as authority:
            with open_handle(
                layout.manifest,
                read=True,
                delete=True,
                share_delete=True,
            ) as expected:
                with pytest.raises(WorkspaceError) as raised:
                    replace_manifest_atomically(candidate, authority.handle, expected)
        assert raised.value.code is WorkspaceErrorCode.IDENTITY_CHANGED
        assert layout.manifest.read_bytes() == previous
        assert candidate_path.exists()
    finally:
        _cleanup_file_handle(candidate)


def test_interrupt_after_native_manifest_rename_resolves_as_committed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, layout = _case(tmp_path)
    previous = layout.manifest.read_bytes()
    candidate_payload = _candidate_bytes(previous)
    candidate = write_new_file(layout.temp / f"{uuid4()}.tmp", candidate_payload)
    real_rename = windows_module._rename_open_file

    def rename_then_interrupt(
        source_handle: windows_module.WindowsHandle,
        *,
        destination: Path,
        replace: bool,
    ) -> tuple[bool, int]:
        outcome = real_rename(source_handle, destination=destination, replace=replace)
        assert outcome[0], outcome
        raise KeyboardInterrupt

    monkeypatch.setattr(windows_module, "_rename_open_file", rename_then_interrupt)
    try:
        with manager.authenticate_case(layout.case_id) as authority:
            with open_handle(
                layout.manifest,
                read=True,
                delete=True,
                share_delete=True,
            ) as expected:
                result = replace_manifest_atomically(candidate, authority.handle, expected)
        assert result.state is ManifestReplaceState.COMMITTED
        candidate.close()
        assert layout.manifest.read_bytes() == candidate_payload
    finally:
        candidate.close()


def test_interrupt_with_unknown_manifest_identity_returns_unknown_without_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, layout = _case(tmp_path)
    previous = layout.manifest.read_bytes()
    candidate_path = layout.temp / f"{uuid4()}.tmp"
    candidate = write_new_file(candidate_path, _candidate_bytes(previous))
    real_resolver = windows_module._resolve_manifest_replace_state
    resolutions = 0

    def first_old_then_unknown(
        destination: Path,
        *,
        candidate_identity: windows_module.FileIdentity,
        expected_identity: windows_module.FileIdentity,
    ) -> ManifestReplaceState:
        nonlocal resolutions
        resolutions += 1
        if resolutions == 1:
            return real_resolver(
                destination,
                candidate_identity=candidate_identity,
                expected_identity=expected_identity,
            )
        return ManifestReplaceState.UNKNOWN

    def interrupt_before_rename(
        _source_handle: windows_module.WindowsHandle,
        *,
        destination: Path,
        replace: bool,
    ) -> tuple[bool, int]:
        del destination, replace
        raise KeyboardInterrupt

    monkeypatch.setattr(
        windows_module,
        "_resolve_manifest_replace_state",
        first_old_then_unknown,
    )
    monkeypatch.setattr(windows_module, "_rename_open_file", interrupt_before_rename)
    try:
        with manager.authenticate_case(layout.case_id) as authority:
            with open_handle(
                layout.manifest,
                read=True,
                delete=True,
                share_delete=True,
            ) as expected:
                result = replace_manifest_atomically(candidate, authority.handle, expected)
        assert result.state is ManifestReplaceState.UNKNOWN
        assert candidate_path.exists()
        assert layout.manifest.read_bytes() == previous
    finally:
        _cleanup_file_handle(candidate)

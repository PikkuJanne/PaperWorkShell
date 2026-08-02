"""Security and privacy evidence for authenticated full-case lifecycle operations."""

from __future__ import annotations

import hashlib
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
from paperworkshell.storage.case_lifecycle import (
    WindowsCaseLifecycleStore,
    build_initial_manifest_bytes,
)
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.workspace import CaseWorkspaceManager

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="PWS-0102 targets the supported Windows lifecycle boundary"
)

FIRST_ID = UUID("40000000-0000-4000-8000-000000000401")
SECOND_ID = UUID("50000000-0000-4000-8000-000000000502")
NOW = datetime(2026, 8, 2, 13, 45, tzinfo=UTC)
CONFIRM = CaseDeletionConfirmation.REMOVE_FROM_APPLICATION_STORAGE


def _quota() -> WorkspaceQuota:
    return WorkspaceQuota(4_000_000, 1_000_000, 1_000_000, 256, 16)


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


def _file_snapshot(path: Path) -> tuple[bytes, int, int, int]:
    stat = path.stat(follow_symlinks=False)
    return (path.read_bytes(), stat.st_ino, stat.st_size, stat.st_mtime_ns)


def _tree_snapshot(root: Path) -> tuple[tuple[str, str, int, int, int, str], ...]:
    records: list[tuple[str, str, int, int, int, str]] = []
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        directories.sort(key=str.casefold)
        files.sort(key=str.casefold)
        current_path = Path(current)
        for name in directories:
            path = current_path / name
            stat = path.stat(follow_symlinks=False)
            records.append(
                (
                    path.relative_to(root).as_posix(),
                    "directory",
                    stat.st_ino,
                    stat.st_size,
                    stat.st_mtime_ns,
                    "",
                )
            )
        for name in files:
            path = current_path / name
            stat = path.stat(follow_symlinks=False)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            records.append(
                (
                    path.relative_to(root).as_posix(),
                    "file",
                    stat.st_ino,
                    stat.st_size,
                    stat.st_mtime_ns,
                    digest,
                )
            )
    return tuple(sorted(records))


def test_delete_preserves_sibling_case_and_outside_sentinels(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    manager = _manager(profile, identifiers=(FIRST_ID, SECOND_ID))
    service = _service(manager)
    first = service.create_case()
    service.close_case(first.case_id)
    second = service.create_case()
    service.close_case(second.case_id)
    first_layout = manager.layout(FIRST_ID)
    second_layout = manager.layout(SECOND_ID)
    (first_layout.temp / "delete-me.bin").write_bytes(b"selected-case")
    sibling = second_layout.temp / "sibling-sentinel.bin"
    sibling.write_bytes(b"sibling-case")
    outside = tmp_path / "outside-export-sentinel.bin"
    outside.write_bytes(b"outside-export")
    sibling_before = _file_snapshot(sibling)
    outside_before = _file_snapshot(outside)

    result = service.delete_case(FIRST_ID, CONFIRM)

    assert result.status is CaseDeletionStatus.COMPLETE
    assert not first_layout.case_root.exists()
    assert second_layout.case_root.is_dir()
    assert _file_snapshot(sibling) == sibling_before
    assert _file_snapshot(outside) == outside_before
    index = service.list_cases()
    assert tuple(summary.case_id for summary in index.cases) == (SECOND_ID,)
    assert index.unavailable == ()


def test_hard_link_rejection_precedes_every_case_removal(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    manager = _manager(profile, identifiers=(FIRST_ID,))
    service = _service(manager)
    service.create_case()
    service.close_case(FIRST_ID)
    layout = manager.layout(FIRST_ID)
    ordinary = layout.cache / "a-ordinary.bin"
    ordinary.write_bytes(b"ordinary-preserved")
    outside = tmp_path / "outside-hardlink.bin"
    outside.write_bytes(b"outside-hardlink")
    linked = layout.temp / "z-hardlink.bin"
    try:
        os.link(outside, linked)
    except OSError as error:
        pytest.skip(f"hard links unavailable on the test volume: {type(error).__name__}")
    tree_before = _tree_snapshot(layout.case_root)
    outside_before = _file_snapshot(outside)

    with pytest.raises(CaseLifecycleError) as raised:
        service.delete_case(FIRST_ID, CONFIRM)

    assert raised.value.code is CaseLifecycleErrorCode.DELETE_FAILED
    assert _tree_snapshot(layout.case_root) == tree_before
    assert _file_snapshot(outside) == outside_before
    assert ordinary.read_bytes() == b"ordinary-preserved"
    assert layout.manifest.exists()


def test_alternate_stream_rejection_precedes_every_case_removal(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    manager = _manager(profile, identifiers=(FIRST_ID,))
    service = _service(manager)
    service.create_case()
    service.close_case(FIRST_ID)
    layout = manager.layout(FIRST_ID)
    ordinary = layout.cache / "a-ordinary.bin"
    ordinary.write_bytes(b"ordinary-preserved")
    streamed = layout.temp / "z-streamed.bin"
    streamed.write_bytes(b"visible-stream-host")
    stream_path = f"{streamed}:synthetic-private"
    try:
        with open(stream_path, "wb") as stream:
            stream.write(b"hidden-stream")
    except OSError as error:
        pytest.skip(
            f"alternate data streams unavailable on the test volume: {type(error).__name__}"
        )
    tree_before = _tree_snapshot(layout.case_root)

    with pytest.raises(CaseLifecycleError) as raised:
        service.delete_case(FIRST_ID, CONFIRM)

    assert raised.value.code is CaseLifecycleErrorCode.DELETE_FAILED
    assert "synthetic-private" not in str(raised.value)
    assert _tree_snapshot(layout.case_root) == tree_before
    with open(stream_path, "rb") as stream:
        assert stream.read() == b"hidden-stream"
    assert ordinary.read_bytes() == b"ordinary-preserved"
    assert layout.manifest.exists()


def test_optional_export_history_must_be_an_authenticated_regular_file(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    manager = _manager(profile, identifiers=(FIRST_ID,))
    service = _service(manager)
    service.create_case()
    service.close_case(FIRST_ID)
    layout = manager.layout(FIRST_ID)
    layout.export_history.mkdir()

    index = service.list_cases()
    with pytest.raises(CaseLifecycleError) as opened:
        service.open_case(FIRST_ID)

    assert index.cases == ()
    assert index.unavailable[0].code is CaseLifecycleErrorCode.MANIFEST_INVALID
    assert opened.value.code is CaseLifecycleErrorCode.MANIFEST_INVALID
    assert layout.manifest.exists()


def test_manifest_and_raw_storage_failures_do_not_leak_paths_or_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    profile = _profile(tmp_path)
    manager = _manager(profile, identifiers=(FIRST_ID,))
    service = _service(manager)
    service.create_case()
    service.close_case(FIRST_ID)
    layout = manager.layout(FIRST_ID)
    canary = "SYNTHETIC-PRIVATE-APPLICANT-CANARY"
    document = json.loads(build_initial_manifest_bytes(FIRST_ID, NOW))
    document[canary] = f"{profile}\\private-document.pdf"
    layout.manifest.write_text(json.dumps(document), encoding="utf-8")

    index = service.list_cases()
    with pytest.raises(CaseLifecycleError) as opened:
        service.open_case(FIRST_ID)
    with pytest.raises(CaseLifecycleError) as deleted:
        service.delete_case(FIRST_ID, CONFIRM)

    rendered = "\n".join(
        (
            repr(index),
            str(opened.value),
            repr(opened.value),
            repr(opened.value.args),
            str(deleted.value),
            repr(deleted.value),
            repr(deleted.value.args),
            caplog.text,
        )
    )
    assert index.cases == ()
    assert index.unavailable[0].code is CaseLifecycleErrorCode.MANIFEST_INVALID
    assert opened.value.code is CaseLifecycleErrorCode.MANIFEST_INVALID
    assert deleted.value.code is CaseLifecycleErrorCode.MANIFEST_INVALID
    assert canary not in rendered
    assert str(profile) not in rendered

    layout.manifest.write_bytes(build_initial_manifest_bytes(FIRST_ID, NOW))

    def fail_with_private_detail(_authority: object, **_kwargs: object) -> None:
        raise OSError(f"{canary}:{profile}")

    monkeypatch.setattr(manager, "remove_case", fail_with_private_detail)
    with pytest.raises(CaseLifecycleError) as raw_failure:
        service.delete_case(FIRST_ID, CONFIRM)

    raw_rendered = (
        f"{raw_failure.value!s}\n{raw_failure.value!r}\n{raw_failure.value.args!r}\n{caplog.text}"
    )
    assert raw_failure.value.code is CaseLifecycleErrorCode.DELETE_FAILED
    assert canary not in raw_rendered
    assert str(profile) not in raw_rendered
    assert layout.manifest.exists()

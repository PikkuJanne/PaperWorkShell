"""Security and privacy evidence for PWS-0109 manifest persistence and recovery."""

from __future__ import annotations

import ast
import copy
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from paperworkshell.domain.manifest_repository import (
    ManifestLoadStatus,
    ManifestRepositoryError,
    ManifestRepositoryErrorCode,
)
from paperworkshell.storage import manifest_repository as repository_storage
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.errors import WorkspaceError, WorkspaceErrorCode
from paperworkshell.storage.manifest_codec import MAX_JSON_STRING_CODEPOINTS
from paperworkshell.storage.manifest_repository import (
    WindowsManifestRepository,
    canonical_manifest_bytes,
)
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.workspace import CaseWorkspaceManager, CaseWorkspacePaths

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="PWS-0109 targets the supported Windows storage boundary"
)

ROOT = Path(__file__).resolve().parents[2]
CASE_ID = UUID("30000000-0000-4000-8000-000000000309")
CREATED_AT = datetime(2026, 8, 3, 9, 0, tzinfo=UTC)


def _quota() -> WorkspaceQuota:
    return WorkspaceQuota(32_000_000, 1_000_000, 20_000_000, 256, 16)


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


def _candidate(document: dict[str, object], *, revision: int) -> dict[str, object]:
    candidate = copy.deepcopy(document)
    candidate["revision"] = revision
    candidate["updated_at"] = (
        (CREATED_AT + timedelta(seconds=revision))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    constraints = candidate["constraints"]
    assert type(constraints) is dict
    constraints["maximum_file_count"] = revision + 1
    return candidate


def _file_snapshot(path: Path) -> tuple[bytes, int, int, int]:
    stat = path.stat(follow_symlinks=False)
    return (path.read_bytes(), stat.st_ino, stat.st_size, stat.st_mtime_ns)


def _tree_snapshot(root: Path) -> tuple[tuple[str, bytes], ...]:
    return tuple(
        (path.relative_to(root).as_posix(), path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


def test_manifest_values_and_raw_storage_failures_never_reach_errors_or_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    manager, layout = _stack(tmp_path)
    repository = WindowsManifestRepository(manager)
    canary = "SYNTHETIC-PRIVATE-MANIFEST-CANARY"
    private_value = f"{layout.case_root}\\private-applicant.pdf"
    document = json.loads(layout.manifest.read_bytes())
    assert type(document) is dict
    document[canary] = private_value
    layout.manifest.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ManifestRepositoryError) as invalid:
        repository.load(CASE_ID)

    rendered = f"{invalid.value!s}\n{invalid.value!r}\n{invalid.value.args!r}\n{caplog.text}"
    assert invalid.value.code is ManifestRepositoryErrorCode.MANIFEST_INVALID
    assert canary not in rendered
    assert private_value not in rendered
    assert str(layout.case_root) not in rendered

    def raw_storage_failure(_authority: object) -> object:
        raise WorkspaceError(
            WorkspaceErrorCode.IDENTITY_CHANGED,
            "filesystem.open",
            f"{canary}:{private_value}",
        )

    monkeypatch.setattr(repository_storage, "_read_raw_manifest", raw_storage_failure)
    with pytest.raises(ManifestRepositoryError) as storage:
        repository.load(CASE_ID)

    storage_rendered = (
        f"{storage.value!s}\n{storage.value!r}\n{storage.value.args!r}\n{caplog.text}"
    )
    assert storage.value.code is ManifestRepositoryErrorCode.MANIFEST_INVALID
    assert canary not in storage_rendered
    assert private_value not in storage_rendered
    assert str(layout.case_root) not in storage_rendered


def test_future_current_is_not_downgraded_from_valid_v1_checkpoint(tmp_path: Path) -> None:
    manager, layout = _stack(tmp_path)
    current = layout.manifest.read_bytes()
    (layout.journal / "latest.json").write_bytes(current)
    future = json.loads(current)
    assert type(future) is dict
    future["schema_version"] = 2
    future["future_shape"] = {"fraction": 1.25, "members": ["unknown"]}
    future["future_text"] = "x" * (MAX_JSON_STRING_CODEPOINTS + 1)
    layout.manifest.write_bytes(
        json.dumps(future, separators=(",", ":"), sort_keys=True).encode() + b"\n"
    )
    before = _tree_snapshot(layout.case_root)

    with pytest.raises(ManifestRepositoryError) as raised:
        WindowsManifestRepository(manager).load(CASE_ID)

    assert raised.value.code is ManifestRepositoryErrorCode.VERSION_UNSUPPORTED
    assert _tree_snapshot(layout.case_root) == before


def test_gap_checkpoint_cannot_replace_a_valid_current_revision(tmp_path: Path) -> None:
    manager, layout = _stack(tmp_path)
    current_document = json.loads(layout.manifest.read_bytes())
    assert type(current_document) is dict
    gap = _candidate(current_document, revision=2)
    (layout.journal / "latest.json").write_bytes(canonical_manifest_bytes(gap))
    before = _tree_snapshot(layout.case_root)

    with pytest.raises(ManifestRepositoryError) as raised:
        WindowsManifestRepository(manager).load(CASE_ID)

    assert raised.value.code is ManifestRepositoryErrorCode.RECOVERY_FAILED
    assert _tree_snapshot(layout.case_root) == before


def test_hard_linked_current_is_rejected_without_mutating_outside_target(
    tmp_path: Path,
) -> None:
    manager, layout = _stack(tmp_path)
    payload = layout.manifest.read_bytes()
    outside = tmp_path / "outside-manifest-sentinel.json"
    outside.write_bytes(payload)
    layout.manifest.unlink()
    try:
        os.link(outside, layout.manifest)
    except OSError as error:
        pytest.skip(f"hard links unavailable on the test volume: {type(error).__name__}")
    outside_before = _file_snapshot(outside)

    with pytest.raises(ManifestRepositoryError) as raised:
        WindowsManifestRepository(manager).load(CASE_ID)

    assert raised.value.code is ManifestRepositoryErrorCode.MANIFEST_INVALID
    assert _file_snapshot(outside) == outside_before
    assert layout.manifest.read_bytes() == payload


def test_hard_linked_checkpoint_is_ignored_without_mutating_outside_target(
    tmp_path: Path,
) -> None:
    manager, layout = _stack(tmp_path)
    outside = tmp_path / "outside-checkpoint-sentinel.json"
    outside.write_bytes(layout.manifest.read_bytes())
    checkpoint = layout.journal / "latest.json"
    try:
        os.link(outside, checkpoint)
    except OSError as error:
        pytest.skip(f"hard links unavailable on the test volume: {type(error).__name__}")
    outside_before = _file_snapshot(outside)
    current_before = _file_snapshot(layout.manifest)

    loaded = WindowsManifestRepository(manager).load(CASE_ID)

    assert loaded.status is ManifestLoadStatus.CURRENT
    assert _file_snapshot(outside) == outside_before
    assert _file_snapshot(layout.manifest) == current_before


def test_alternate_stream_checkpoint_is_not_promoted(tmp_path: Path) -> None:
    manager, layout = _stack(tmp_path)
    checkpoint = layout.journal / "latest.json"
    checkpoint.write_bytes(layout.manifest.read_bytes())
    stream_path = f"{checkpoint}:synthetic-private"
    try:
        with open(stream_path, "wb") as stream:
            stream.write(b"private-stream-canary")
    except OSError as error:
        pytest.skip(
            f"alternate data streams unavailable on the test volume: {type(error).__name__}"
        )
    current_before = _file_snapshot(layout.manifest)

    loaded = WindowsManifestRepository(manager).load(CASE_ID)

    assert loaded.status is ManifestLoadStatus.CURRENT
    assert _file_snapshot(layout.manifest) == current_before
    with open(stream_path, "rb") as stream:
        assert stream.read() == b"private-stream-canary"


def test_partial_stage_failure_preserves_current_sibling_and_outside_sentinels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, layout = _stack(tmp_path)
    repository = WindowsManifestRepository(manager)
    opened = repository.load(CASE_ID).snapshot
    candidate = _candidate(opened.document, revision=1)
    sibling = layout.temp / "sibling-sentinel.bin"
    sibling.write_bytes(b"sibling-preserved")
    outside = tmp_path / "outside-sentinel.bin"
    outside.write_bytes(b"outside-preserved")
    manifest_before = _file_snapshot(layout.manifest)
    sibling_before = _file_snapshot(sibling)
    outside_before = _file_snapshot(outside)
    raw_canary = f"RAW-STORAGE-FAILURE:{outside}"

    def partial_write(path: Path, payload: bytes) -> object:
        path.write_bytes(payload[: max(1, len(payload) // 2)])
        raise WorkspaceError(
            WorkspaceErrorCode.CREATE_FAILED,
            "filesystem.write",
            raw_canary,
        )

    monkeypatch.setattr(repository_storage, "write_new_file", partial_write)

    with pytest.raises(ManifestRepositoryError) as raised:
        repository.save(
            CASE_ID,
            candidate,
            expected_revision=opened.revision,
            expected_sha256=opened.sha256,
            expected_cas_token=opened.cas_token,
        )

    assert raised.value.code is ManifestRepositoryErrorCode.STORAGE_FAILED
    assert raw_canary not in f"{raised.value!s}\n{raised.value!r}\n{raised.value.args!r}"
    assert _file_snapshot(layout.manifest) == manifest_before
    assert _file_snapshot(sibling) == sibling_before
    assert _file_snapshot(outside) == outside_before
    assert not (layout.journal / "latest.json").exists()


def _qualified_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _qualified_name(node.value)
        return None if parent is None else f"{parent}.{node.attr}"
    return None


def test_manifest_layers_import_no_shell_network_or_logging_capabilities() -> None:
    paths = (
        ROOT / "src" / "paperworkshell" / "domain" / "manifest_repository.py",
        ROOT / "src" / "paperworkshell" / "domain" / "manifest_validation.py",
        ROOT / "src" / "paperworkshell" / "app" / "manifest_repository.py",
        ROOT / "src" / "paperworkshell" / "storage" / "manifest_codec.py",
        ROOT / "src" / "paperworkshell" / "storage" / "manifest_repository.py",
    )
    prohibited_roots = {
        "asyncio",
        "http",
        "logging",
        "requests",
        "socket",
        "subprocess",
        "urllib",
    }
    prohibited_calls = {
        "eval",
        "exec",
        "os.popen",
        "os.system",
    }

    findings: list[str] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.partition(".")[0] in prohibited_roots:
                        findings.append(f"{path.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.partition(".")[0] in prohibited_roots:
                    findings.append(f"{path.name}: from {module}")
            elif isinstance(node, ast.Call):
                name = _qualified_name(node.func)
                if name in prohibited_calls:
                    findings.append(f"{path.name}: call {name}")

    assert findings == []

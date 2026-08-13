"""Windows end-to-end evidence for PWS-0109 atomic manifest persistence."""

from __future__ import annotations

import copy
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from multiprocessing import get_context
from pathlib import Path
from threading import Barrier
from typing import Any
from uuid import UUID

import pytest

from paperworkshell.app.manifest_repository import ManifestAutosaveService
from paperworkshell.domain.manifest_repository import (
    ManifestAutosaveStatus,
    ManifestLoadStatus,
    ManifestRepositoryError,
    ManifestRepositoryErrorCode,
    ManifestSaveResult,
)
from paperworkshell.domain.page_operations import (
    PageCommandHistory,
    RotatePageCommand,
    SequenceChangeCommand,
    SequenceChangeReason,
    SetGroupCommand,
)
from paperworkshell.storage import manifest_repository as repository_storage
from paperworkshell.storage._windows import open_handle, replace_manifest_atomically
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.errors import WorkspaceError, WorkspaceErrorCode
from paperworkshell.storage.manifest_codec import MAX_JSON_STRING_CODEPOINTS
from paperworkshell.storage.manifest_repository import WindowsManifestRepository
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.workspace import CaseWorkspaceManager, CaseWorkspacePaths
from tests.helpers.storage_delay import DeterministicStorageDelay, StorageDelayOperation

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="PWS-0109 targets the supported Windows storage boundary"
)

CASE_ID = UUID("10000000-0000-4000-8000-000000000109")
CREATED_AT = datetime(2026, 8, 3, 9, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]
REPRESENTATIVE_MANIFEST = ROOT / "schemas" / "examples" / "case-manifest-v2-representative.json"


def _quota(*, maximum_case_bytes: int = 32_000_000) -> WorkspaceQuota:
    return WorkspaceQuota(
        maximum_case_bytes=maximum_case_bytes,
        maximum_cache_bytes=min(1_000_000, maximum_case_bytes),
        maximum_temp_bytes=min(20_000_000, maximum_case_bytes),
        maximum_entries=256,
        maximum_depth=16,
    )


def _stack(
    tmp_path: Path,
    *,
    created_at: datetime = CREATED_AT,
) -> tuple[CaseWorkspaceManager, CaseWorkspacePaths]:
    profile = tmp_path / "profile"
    profile.mkdir()
    manager = CaseWorkspaceManager(
        profile_root=profile,
        quota=_quota(),
        id_factory=lambda: CASE_ID,
    )
    lease = WindowsCaseLifecycleStore(manager, clock=lambda: created_at).create_case()
    lease.close()
    return manager, manager.layout(CASE_ID)


def _candidate(
    document: dict[str, object],
    *,
    revision: int,
    marker: int,
) -> dict[str, object]:
    candidate = copy.deepcopy(document)
    candidate["revision"] = revision
    candidate["updated_at"] = (
        (CREATED_AT + timedelta(seconds=revision))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    constraints = candidate["constraints"]
    assert type(constraints) is dict
    constraints["maximum_file_count"] = marker
    return candidate


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _hold_case_transaction(
    profile: str,
    start: Any,
    active_count: Any,
    overlap_count: Any,
) -> None:
    manager = CaseWorkspaceManager(profile_root=Path(profile), quota=_quota())
    start.wait(timeout=10)
    with manager.authenticate_case(CASE_ID) as authority:
        with authority.transaction_guard():
            with active_count.get_lock():
                active_count.value += 1
                if active_count.value > 1:
                    overlap_count.value += 1
            try:
                time.sleep(0.25)
            finally:
                with active_count.get_lock():
                    active_count.value -= 1


def test_save_fresh_load_and_corrupted_current_recovery(tmp_path: Path) -> None:
    manager, layout = _stack(tmp_path)
    repository = WindowsManifestRepository(manager)
    opened = repository.load(CASE_ID)
    first_candidate = _candidate(opened.snapshot.document, revision=1, marker=11)

    first = repository.save(
        CASE_ID,
        first_candidate,
        expected_revision=opened.snapshot.revision,
        expected_sha256=opened.snapshot.sha256,
        expected_cas_token=opened.snapshot.cas_token,
    )

    assert first.snapshot.revision == 1
    assert layout.manifest.read_bytes() == (layout.journal / "latest.json").read_bytes()
    fresh = WindowsManifestRepository(
        CaseWorkspaceManager(profile_root=manager.profile_root, quota=_quota())
    )
    reloaded = fresh.load(CASE_ID)
    assert reloaded.status is ManifestLoadStatus.CURRENT
    assert reloaded.snapshot.document == first_candidate

    second_candidate = _candidate(reloaded.snapshot.document, revision=2, marker=22)
    second = fresh.save(
        CASE_ID,
        second_candidate,
        expected_revision=reloaded.snapshot.revision,
        expected_sha256=reloaded.snapshot.sha256,
        expected_cas_token=reloaded.snapshot.cas_token,
    )
    checkpoint = (layout.journal / "latest.json").read_bytes()
    layout.manifest.write_bytes(b'{"schema_version":1')

    recovered = WindowsManifestRepository(
        CaseWorkspaceManager(profile_root=manager.profile_root, quota=_quota())
    ).load(CASE_ID)

    assert recovered.status is ManifestLoadStatus.RECOVERED
    assert recovered.snapshot.revision == second.snapshot.revision == 2
    assert recovered.snapshot.document == second_candidate
    assert layout.manifest.read_bytes() == checkpoint


def test_case_transaction_guard_serializes_separate_processes(tmp_path: Path) -> None:
    manager, _layout = _stack(tmp_path)
    context = get_context("spawn")
    start = context.Barrier(3)
    active_count = context.Value("i", 0)
    overlap_count = context.Value("i", 0)
    processes = tuple(
        context.Process(
            target=_hold_case_transaction,
            args=(str(manager.profile_root), start, active_count, overlap_count),
        )
        for _ in range(2)
    )

    for process in processes:
        process.start()
    start.wait(timeout=10)
    for process in processes:
        process.join(timeout=15)

    assert [process.exitcode for process in processes] == [0, 0]
    assert overlap_count.value == 0
    assert active_count.value == 0
    for process in processes:
        process.close()


@pytest.mark.parametrize("failure_kind", ["partial_write", "disk_full"])
def test_stage_failure_never_replaces_last_valid_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
) -> None:
    manager, layout = _stack(tmp_path)
    repository = WindowsManifestRepository(manager)
    opened = repository.load(CASE_ID).snapshot
    candidate = _candidate(opened.document, revision=1, marker=7)
    manifest_before = layout.manifest.read_bytes()
    tree_before = _tree_snapshot(layout.case_root)

    def fail_write(path: Path, payload: bytes) -> object:
        if failure_kind == "partial_write":
            path.write_bytes(payload[: max(1, len(payload) // 3)])
            raise WorkspaceError(
                WorkspaceErrorCode.CREATE_FAILED,
                "filesystem.write",
                "Synthetic bounded write failure.",
            )
        raise WorkspaceError(
            WorkspaceErrorCode.CREATE_FAILED,
            "filesystem.write",
            "Synthetic operating-system disk-full write failure.",
        )

    monkeypatch.setattr(repository_storage, "write_new_file", fail_write)

    with pytest.raises(ManifestRepositoryError) as raised:
        repository.save(
            CASE_ID,
            candidate,
            expected_revision=opened.revision,
            expected_sha256=opened.sha256,
            expected_cas_token=opened.cas_token,
        )

    assert raised.value.code is ManifestRepositoryErrorCode.STORAGE_FAILED
    assert layout.manifest.read_bytes() == manifest_before
    assert (layout.journal / "latest.json").exists() is False
    after = _tree_snapshot(layout.case_root)
    assert {
        name: payload for name, payload in after.items() if not name.startswith("temp/")
    } == tree_before


def test_future_current_and_future_candidate_fail_before_mutation(tmp_path: Path) -> None:
    manager, layout = _stack(tmp_path)
    repository = WindowsManifestRepository(manager)
    opened = repository.load(CASE_ID).snapshot
    future_candidate = _candidate(opened.document, revision=1, marker=3)
    future_candidate["schema_version"] = 3
    before_candidate = _tree_snapshot(layout.case_root)

    with pytest.raises(ManifestRepositoryError) as candidate_error:
        repository.save(
            CASE_ID,
            future_candidate,
            expected_revision=opened.revision,
            expected_sha256=opened.sha256,
            expected_cas_token=opened.cas_token,
        )

    assert candidate_error.value.code is ManifestRepositoryErrorCode.VERSION_UNSUPPORTED
    assert _tree_snapshot(layout.case_root) == before_candidate

    future_current = opened.document
    future_current["schema_version"] = 3
    layout.manifest.write_bytes(
        json.dumps(future_current, separators=(",", ":"), sort_keys=True).encode() + b"\n"
    )
    before_open = _tree_snapshot(layout.case_root)

    with pytest.raises(ManifestRepositoryError) as current_error:
        repository.load(CASE_ID)

    assert current_error.value.code is ManifestRepositoryErrorCode.VERSION_UNSUPPORTED
    assert _tree_snapshot(layout.case_root) == before_open


def test_future_checkpoint_fails_closed_without_mutating_valid_current(tmp_path: Path) -> None:
    manager, layout = _stack(tmp_path)
    repository = WindowsManifestRepository(manager)
    opened = repository.load(CASE_ID).snapshot
    candidate = _candidate(opened.document, revision=1, marker=4)
    future_checkpoint = copy.deepcopy(candidate)
    future_checkpoint["schema_version"] = 3
    future_checkpoint["future_fraction"] = 1.25
    future_checkpoint["future_text"] = "x" * (MAX_JSON_STRING_CODEPOINTS + 1)
    (layout.journal / "latest.json").write_bytes(
        json.dumps(future_checkpoint, separators=(",", ":"), sort_keys=True).encode() + b"\n"
    )
    before = _tree_snapshot(layout.case_root)

    with pytest.raises(ManifestRepositoryError) as load_error:
        repository.load(CASE_ID)
    with pytest.raises(ManifestRepositoryError) as save_error:
        repository.save(
            CASE_ID,
            candidate,
            expected_revision=opened.revision,
            expected_sha256=opened.sha256,
            expected_cas_token=opened.cas_token,
        )

    assert load_error.value.code is ManifestRepositoryErrorCode.VERSION_UNSUPPORTED
    assert save_error.value.code is ManifestRepositoryErrorCode.VERSION_UNSUPPORTED
    assert _tree_snapshot(layout.case_root) == before


def test_malformed_checkpoint_cannot_displace_valid_current(tmp_path: Path) -> None:
    manager, layout = _stack(tmp_path)
    current_before = layout.manifest.read_bytes()
    (layout.journal / "latest.json").write_bytes(b'{"schema_version":1')

    loaded = WindowsManifestRepository(manager).load(CASE_ID)

    assert loaded.status is ManifestLoadStatus.CURRENT
    assert loaded.snapshot.revision == 0
    assert layout.manifest.read_bytes() == current_before
    assert (layout.journal / "latest.json").read_bytes() == b'{"schema_version":1'


def test_interruption_after_checkpoint_recovers_exact_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, layout = _stack(tmp_path)
    repository = WindowsManifestRepository(manager)
    opened = repository.load(CASE_ID).snapshot
    candidate = _candidate(opened.document, revision=1, marker=19)
    previous = layout.manifest.read_bytes()

    def interrupt_replace(*_args: object, **_kwargs: object) -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr(
        repository_storage,
        "replace_manifest_atomically",
        interrupt_replace,
    )

    with pytest.raises(KeyboardInterrupt):
        repository.save(
            CASE_ID,
            candidate,
            expected_revision=opened.revision,
            expected_sha256=opened.sha256,
            expected_cas_token=opened.cas_token,
        )

    assert layout.manifest.read_bytes() == previous
    assert json.loads((layout.journal / "latest.json").read_bytes()) == candidate

    monkeypatch.undo()
    recovered = WindowsManifestRepository(
        CaseWorkspaceManager(profile_root=manager.profile_root, quota=_quota())
    ).load(CASE_ID)

    assert recovered.status is ManifestLoadStatus.RECOVERED
    assert recovered.snapshot.document == candidate
    assert layout.manifest.read_bytes() == (layout.journal / "latest.json").read_bytes()


def test_slow_atomic_rename_keeps_prior_manifest_authoritative_until_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, layout = _stack(tmp_path)
    repository = WindowsManifestRepository(manager)
    opened = repository.load(CASE_ID).snapshot
    candidate = _candidate(opened.document, revision=1, marker=23)
    previous = layout.manifest.read_bytes()
    delay = DeterministicStorageDelay(frozenset({StorageDelayOperation.RENAME}))
    real_replace = replace_manifest_atomically

    def delayed_replace(*args: object, **kwargs: object) -> object:
        delay.checkpoint(StorageDelayOperation.RENAME)
        return real_replace(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(repository_storage, "replace_manifest_atomically", delayed_replace)
    with open_handle(
        layout.manifest,
        read=True,
        share_delete=True,
        share_write=True,
    ) as retained_previous:
        with ThreadPoolExecutor(max_workers=1) as pool:
            saving = pool.submit(
                repository.save,
                CASE_ID,
                candidate,
                expected_revision=opened.revision,
                expected_sha256=opened.sha256,
                expected_cas_token=opened.cas_token,
            )
            assert delay.entered.wait(timeout=2)
            assert not saving.done()
            assert retained_previous.read_bytes(maximum_bytes=len(previous)) == previous

            delay.release.set()
            saved = saving.result(timeout=2)

    assert saved.snapshot.document == candidate
    assert layout.manifest.read_bytes() == (layout.journal / "latest.json").read_bytes()
    assert delay.observed == (StorageDelayOperation.RENAME,)


def test_same_snapshot_concurrency_has_one_exact_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, layout = _stack(tmp_path)
    repository = WindowsManifestRepository(manager)
    opened = repository.load(CASE_ID).snapshot
    candidates = (
        _candidate(opened.document, revision=1, marker=31),
        _candidate(opened.document, revision=1, marker=32),
    )
    barrier = Barrier(2)
    real_commit = repository_storage.commit_manifest_transaction

    def synchronized_commit(**kwargs: object) -> object:
        barrier.wait(timeout=10)
        return real_commit(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        repository_storage,
        "commit_manifest_transaction",
        synchronized_commit,
    )

    def save(candidate: dict[str, object]) -> ManifestSaveResult | ManifestRepositoryError:
        try:
            return repository.save(
                CASE_ID,
                candidate,
                expected_revision=opened.revision,
                expected_sha256=opened.sha256,
                expected_cas_token=opened.cas_token,
            )
        except ManifestRepositoryError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(save, candidates))

    winners = tuple(item for item in outcomes if isinstance(item, ManifestSaveResult))
    losers = tuple(item for item in outcomes if isinstance(item, ManifestRepositoryError))
    assert len(winners) == len(losers) == 1
    assert losers[0].code in {
        ManifestRepositoryErrorCode.REVISION_CONFLICT,
        ManifestRepositoryErrorCode.COMMIT_UNCERTAIN,
    }
    manifest = json.loads(layout.manifest.read_bytes())
    assert manifest == winners[0].snapshot.document
    assert (layout.journal / "latest.json").read_bytes() == layout.manifest.read_bytes()


def test_representative_property_style_save_load_equivalence(tmp_path: Path) -> None:
    manager, _layout = _stack(tmp_path)
    repository = WindowsManifestRepository(manager)
    current = repository.load(CASE_ID).snapshot

    for revision in range(1, 13):
        candidate = _candidate(
            current.document,
            revision=revision,
            marker=(revision * 17) % 101 + 1,
        )
        saved = repository.save(
            CASE_ID,
            candidate,
            expected_revision=current.revision,
            expected_sha256=current.sha256,
            expected_cas_token=current.cas_token,
        )
        current = (
            WindowsManifestRepository(
                CaseWorkspaceManager(profile_root=manager.profile_root, quota=_quota())
            )
            .load(CASE_ID)
            .snapshot
        )
        assert current.document == candidate
        assert current.sha256 == saved.snapshot.sha256


def test_full_representative_graph_save_load_equivalence(tmp_path: Path) -> None:
    representative_created = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
    manager, _layout = _stack(tmp_path, created_at=representative_created)
    repository = WindowsManifestRepository(manager)
    opened = repository.load(CASE_ID).snapshot
    candidate = json.loads(REPRESENTATIVE_MANIFEST.read_bytes())
    assert type(candidate) is dict
    candidate["case_id"] = str(CASE_ID)
    candidate["revision"] = 1

    saved = repository.save(
        CASE_ID,
        candidate,
        expected_revision=opened.revision,
        expected_sha256=opened.sha256,
        expected_cas_token=opened.cas_token,
    )
    reloaded = WindowsManifestRepository(
        CaseWorkspaceManager(profile_root=manager.profile_root, quota=_quota())
    ).load(CASE_ID)

    assert all(
        candidate[name]
        for name in (
            "sources",
            "pages",
            "page_operations",
            "assets",
            "overlays",
            "assignments",
            "confirmations",
        )
    )
    assert reloaded.snapshot.document == candidate
    assert reloaded.snapshot.sha256 == saved.snapshot.sha256


def test_real_repository_autosave_coalesces_to_exact_final_state(tmp_path: Path) -> None:
    manager, layout = _stack(tmp_path)
    repository = WindowsManifestRepository(manager)
    opened = repository.load(CASE_ID).snapshot
    autosave = ManifestAutosaveService(
        repository,
        CASE_ID,
        utc_now=lambda: CREATED_AT + timedelta(minutes=1),
    )

    for marker in range(1, 101):
        candidate = _candidate(opened.document, revision=opened.revision, marker=marker)
        generation = autosave.queue(candidate)
    result = autosave.flush()
    reloaded = WindowsManifestRepository(
        CaseWorkspaceManager(profile_root=manager.profile_root, quota=_quota())
    ).load(CASE_ID)

    assert generation == 100
    assert result.status is ManifestAutosaveStatus.SAVED
    assert result.saved_generation == 100
    assert reloaded.snapshot.revision == 1
    constraints = reloaded.snapshot.document["constraints"]
    assert type(constraints) is dict
    assert constraints["maximum_file_count"] == 100
    assert layout.manifest.read_bytes() == (layout.journal / "latest.json").read_bytes()


def test_page_operation_history_autosave_reload_replays_equivalently(
    tmp_path: Path,
) -> None:
    representative_created = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
    manager, layout = _stack(tmp_path, created_at=representative_created)
    repository = WindowsManifestRepository(manager)
    opened = repository.load(CASE_ID).snapshot

    representative = json.loads(REPRESENTATIVE_MANIFEST.read_bytes())
    assert type(representative) is dict
    representative["case_id"] = str(CASE_ID)
    representative["revision"] = 1
    saved = repository.save(
        CASE_ID,
        representative,
        expected_revision=opened.revision,
        expected_sha256=opened.sha256,
        expected_cas_token=opened.cas_token,
    )

    page_one = UUID("30000000-0000-4000-8000-000000000001")
    page_two = UUID("30000000-0000-4000-8000-000000000002")
    page_three = UUID("30000000-0000-4000-8000-000000000003")
    existing_group = UUID("90000000-0000-4000-8000-000000000001")
    operation_ids = iter(
        (
            UUID("50000000-0000-4000-8000-000000000009"),
            UUID("50000000-0000-4000-8000-00000000000a"),
            UUID("50000000-0000-4000-8000-00000000000b"),
        )
    )

    history = PageCommandHistory.from_snapshot(saved.snapshot)
    history = history.execute(
        SequenceChangeCommand(
            reason=SequenceChangeReason.RESTORE,
            after_page_ids=(page_three, page_two, page_one),
        ),
        id_factory=operation_ids.__next__,
    )
    history = history.execute(
        RotatePageCommand(page_id=page_two, quarter_turns_clockwise=3),
        id_factory=operation_ids.__next__,
    )
    history = history.execute(
        SetGroupCommand(
            page_ids=(page_three, page_two),
            group_id=existing_group,
        ),
        id_factory=operation_ids.__next__,
    )

    before_undo = history
    history = history.undo()
    assert history.can_redo
    history = history.redo()
    assert history.operations == before_undo.operations
    assert history.projection == before_undo.projection

    original_document = saved.snapshot.document
    candidate = history.to_candidate(saved.snapshot)
    assert {
        key: value
        for key, value in candidate.items()
        if key not in {"page_operations", "page_sequence"}
    } == {
        key: value
        for key, value in original_document.items()
        if key not in {"page_operations", "page_sequence"}
    }
    assert candidate["revision"] == saved.snapshot.revision
    assert candidate["page_sequence"] == [
        str(page_three),
        str(page_two),
        str(page_one),
    ]

    autosave = ManifestAutosaveService(
        repository,
        CASE_ID,
        utc_now=lambda: representative_created + timedelta(minutes=45),
    )
    generation = autosave.queue(candidate)
    result = autosave.flush()

    assert generation == 1
    assert result.status is ManifestAutosaveStatus.SAVED
    assert result.snapshot is not None
    assert result.snapshot.revision == 2

    reloaded = WindowsManifestRepository(
        CaseWorkspaceManager(
            profile_root=manager.profile_root,
            quota=_quota(),
        )
    ).load(CASE_ID)
    replayed = PageCommandHistory.from_snapshot(reloaded.snapshot)

    assert reloaded.snapshot.revision == 2
    assert replayed.operations == history.operations
    assert replayed.projection == history.projection
    assert not replayed.can_undo
    assert not replayed.can_redo
    assert layout.manifest.read_bytes() == (layout.journal / "latest.json").read_bytes()

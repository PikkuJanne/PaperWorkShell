"""Model snapshot tests: fake Git/Gitleaks, private temporary bytes only."""

from __future__ import annotations

import os
import stat
import subprocess
from collections.abc import Iterator, Sequence
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tools import dev
from tools.validate_synthetic_fixtures import RepositoryPrivacyFinding, _repository_inspector


def _model_object_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep these snapshot models at their existing pure object-read seam.

    Binary transport/framing and its accounting are exercised independently in
    test_git_object_batches; these fakes deliberately do not launch Git.
    """

    def read_objects(
        git: str,
        root: Path,
        objects: Sequence[tuple[str, str, int]],
        category: str,
        budget: dev._SecretScanEnumerationBudget,
    ) -> Iterator[bytes]:
        for index, (oid, kind, size) in enumerate(objects, start=1):
            label = f"{category} item {index}"
            if kind == "blob":
                yield dev._read_git_blob(git, root, oid, size, label, budget)
            else:
                yield dev._read_git_object(git, root, oid, kind, size, label, budget)

    monkeypatch.setattr(dev, "_read_git_objects", read_objects)


def _snapshot(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    message: bytes = b"synthetic commit message\n",
    untracked: bool = False,
) -> tuple[Path, dev._GitHistoryRoots, list[bytes]]:
    root.mkdir()
    config = b'title = "Synthetic scanner model"\n[extend]\nuseDefault = true\n'
    body = b"synthetic complete source bytes\n"
    (root / ".gitleaks.toml").write_bytes(config)
    (root / "baseline.txt").write_bytes(body)
    paths = [Path(".gitleaks.toml"), Path("baseline.txt")]
    entries = [
        dev._PathBlobEntry(path, str(index) * 40, len(data))
        for index, (path, data) in enumerate(zip(paths, (config, body), strict=True), 1)
    ]
    commit = b"tree " + b"0" * 40 + b"\n\n" + message
    roots = dev._GitHistoryRoots(
        (root,), (b"HEAD", b"refs/heads/main"), ("3" * 40,), (), ("3" * 40,), ("3" * 40,), (), ()
    )
    monkeypatch.setattr(dev, "_git_executable", lambda _budget: "model-git")
    monkeypatch.setattr(dev, "_gitleaks_executable", lambda _budget: "model-gitleaks")
    for name in (
        "_require_git_worktree_root",
        "_require_local_ref_storage",
        "_require_complete_git_history",
    ):
        monkeypatch.setattr(dev, name, lambda *_args: None)
    monkeypatch.setattr(dev, "_history_roots", lambda *_args: roots)
    monkeypatch.setattr(dev, "_historical_path_entries", lambda *_args: entries)
    monkeypatch.setattr(dev, "_index_entries", lambda *_args: entries[:1] if untracked else entries)
    monkeypatch.setattr(
        dev,
        "_historical_objects",
        lambda *_args: (
            [(entry.object_id, entry.object_size) for entry in entries],
            [("3" * 40, "commit", len(commit))],
        ),
    )
    monkeypatch.setattr(
        dev,
        "_git_paths",
        lambda _git, _root, args, _budget: (
            (paths[:1] if untracked else paths)
            if "--cached" in args
            else paths[1:]
            if untracked
            else []
        ),
    )

    def read_blob(
        _git: str,
        _root: Path,
        object_id: str,
        _size: int,
        _label: str,
        budget: dev._SecretScanEnumerationBudget,
    ) -> bytes:
        content = {"1" * 40: config, "2" * 40: body}[object_id]
        budget.consume_work(len(content))
        return content

    def read_object(
        _git: str,
        _root: Path,
        _object: str,
        _type: str,
        _size: int,
        _label: str,
        budget: dev._SecretScanEnumerationBudget,
    ) -> bytes:
        budget.consume_work(len(commit))
        return commit

    monkeypatch.setattr(dev, "_read_git_blob", read_blob)
    monkeypatch.setattr(dev, "_read_git_object", read_object)
    _model_object_reads(monkeypatch)
    payloads: list[bytes] = []

    def capture(argv: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        payload = kwargs.get("input_bytes", b"")
        payloads.append(payload)
        return subprocess.CompletedProcess(
            argv, 42 if b"SYNTHETIC_SECOND_GROUP_SECRET" in payload else 0, b"", b""
        )

    monkeypatch.setattr(dev, "_capture_bytes", capture)
    monkeypatch.setattr(dev, "_scan_mirror", lambda *_args: None)
    control = root.parent / "controls"
    control.mkdir()
    ignore = control / "empty.ignore"
    ignore.write_bytes(b"")
    return ignore, roots, payloads


def test_two_logical_groups_preserve_complete_model_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    ignore, _roots, payloads = _snapshot(root, monkeypatch)
    monkeypatch.setattr(dev, "MAX_SECRET_SCAN_ITEMS", 6)
    scanned = dev._scan_repository_checked(root, ignore, dev._SecretScanEnumerationBudget())
    inspected: list[bytes] = []
    summary = dev.inspect_repository_contents(root, inspected.append)
    assert scanned.tracked_count == summary.tracked_count == 2
    assert summary.history_blob_count == 2 and summary.history_message_object_count == 1
    assert inspected.count(b"baseline.txt") == 2
    assert inspected.count(b"synthetic complete source bytes\n") == 2
    assert len(payloads) >= 2


def test_untracked_exact_completed_path_coverage_skips_only_repeated_mirror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    ignore, _roots, payloads = _snapshot(root, monkeypatch, untracked=True)

    def unexpected(*_args: Any, **_kwargs: Any) -> None:
        if _args[2]:
            raise AssertionError("Exact completed historical path bytes must reuse their mirror")

    monkeypatch.setattr(dev, "_scan_working_path_batch", unexpected)
    summary = dev._scan_repository_checked(root, ignore, dev._SecretScanEnumerationBudget())
    assert summary.tracked_count == 1 and summary.untracked_count == 1
    assert any(b"synthetic complete source bytes\n" in data for data in payloads)


def test_private_alias_preserves_both_names_with_one_payload_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = b"synthetic full rule-sensitive bytes\n".ljust(4096, b"x")
    entry = dev._PathBlobEntry(Path("node_modules/proof.tf"), "1" * 40, len(body))

    def read(
        _git: str,
        _root: Path,
        _oid: str,
        _size: int,
        _label: str,
        budget: dev._SecretScanEnumerationBudget,
    ) -> bytes:
        budget.consume_work(len(body))
        return body

    observed: list[tuple[str, int]] = []

    def inspect(
        _tool: str,
        _ignore: Path,
        mirror: Path,
        _target: Path,
        _label: str,
        _budget: dev._SecretScanEnumerationBudget,
    ) -> None:
        for path in mirror.rglob("*"):
            if path.is_file():
                assert path.read_bytes() == dev.RAW_SCAN_SENTINEL + body
                observed.append((path.name, path.stat().st_nlink))

    monkeypatch.setattr(dev, "_read_git_blob", read)
    _model_object_reads(monkeypatch)
    monkeypatch.setattr(dev, "_scan_mirror", inspect)
    monkeypatch.setattr(dev, "MAX_SECRET_SCAN_WORK_BYTES", 10_000)
    budget = dev._SecretScanEnumerationBudget()
    dev._scan_path_aware_blobs(
        "model-gitleaks",
        "model-git",
        tmp_path,
        tmp_path / "ignore",
        [entry],
        category="private alias model",
        budget=budget,
    )
    assert sorted(observed) == [("proof.tf", 2), ("pws-rule.tf", 2)]
    assert budget.work_bytes < 10_000
    assert budget.work_bytes == dev._path_blob_read_work([entry]) + dev._path_blob_mirror_work(
        [entry]
    )


def test_second_group_message_secret_is_not_hidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    ignore, _roots, _payloads = _snapshot(
        root, monkeypatch, message=b"SYNTHETIC_SECOND_GROUP_SECRET\n"
    )
    monkeypatch.setattr(dev, "MAX_SECRET_SCAN_ITEMS", 6)
    with pytest.raises(dev.SecretScanError) as finding:
        dev._scan_repository_checked(root, ignore, dev._SecretScanEnumerationBudget())
    assert finding.value.code == "SECRET_SCAN_FINDING"


def test_second_group_unapproved_document_message_is_not_hidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    _ignore, _roots, _payloads = _snapshot(
        root,
        monkeypatch,
        message=bytes((37, 80, 68, 70, 45)) + b"1.7\nsynthetic unapproved message\n",
    )
    monkeypatch.setattr(dev, "MAX_SECRET_SCAN_ITEMS", 6)
    with pytest.raises(RepositoryPrivacyFinding) as finding:
        dev.inspect_repository_contents(root, _repository_inspector(frozenset()))
    assert finding.value.code == "FIXTURE_UNINDEXED_DOCUMENT"


@pytest.mark.parametrize("counts", [(10_001,), (10_000, 10_000, 1), (-1,)])
def test_snapshot_category_and_two_group_overflow_remain_closed(counts: tuple[int, ...]) -> None:
    with pytest.raises(dev.SecretScanError) as failure:
        dev._require_snapshot_item_capacity(counts, path_count=0)
    assert failure.value.code == "SECRET_SCAN_INPUT_TOO_LARGE"
    dev._require_snapshot_item_capacity((10_000, 10_000), path_count=0)


def test_raw_epochs_bound_bodies_and_digests_and_preserve_all_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dev, "MAX_SECRET_SCAN_ITEMS", 2)
    payloads: list[bytes] = []

    def capture(argv: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        payloads.append(kwargs["input_bytes"])
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(dev, "_capture_bytes", capture)
    budget = dev._SecretScanEnumerationBudget()
    batcher = dev._SecretScanBatcher("model", tmp_path, tmp_path / "ignore", budget)
    snapshots = []
    for content in (b"first", b"second", b"third", b"first"):
        batcher.add("model item", content)
        snapshots.append((len(batcher._items), len(batcher._seen_digests), budget.work_bytes))
    batcher.flush()
    assert max(x[0] for x in snapshots) <= 2 and max(x[1] for x in snapshots) <= 2
    assert [x[2] for x in snapshots] == sorted(x[2] for x in snapshots)
    assert len(payloads) == 2 and sum(data.count(b"first") for data in payloads) == 2
    assert all(token in b"".join(payloads) for token in (b"first", b"second", b"third"))


def test_failed_raw_flush_does_not_clear_prior_epoch_or_reset_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dev, "MAX_SECRET_SCAN_ITEMS", 2)
    monkeypatch.setattr(
        dev,
        "_capture_bytes",
        lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 42, b"", b""),
    )
    budget = dev._SecretScanEnumerationBudget()
    batcher = dev._SecretScanBatcher("model", tmp_path, tmp_path / "ignore", budget)
    batcher.add("first", b"one")
    batcher.add("second", b"two")
    before = set(batcher._seen_digests)
    work = budget.work_bytes
    with pytest.raises(dev.SecretScanError):
        batcher.add("third", b"three")
    assert batcher._seen_digests == before and len(batcher._items) == 2
    assert budget.work_bytes >= work


@pytest.mark.parametrize(
    "mode",
    [
        "outside",
        "existing",
        "changed",
        "same_size_mtime",
        "extra_link",
        "unsupported",
        "copied",
        "redirected_parent",
    ],
)
def test_alias_scope_identity_and_capability_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    root = tmp_path / "private"
    root.mkdir()
    source = root / "source/input.tf"
    target = root / "path-rule-shadow/item-00000001/pws-rule.tf"
    budget = dev._SecretScanEnumerationBudget()
    prepared = dev._write_mirror_input(source, b"synthetic payload", "model source", budget)
    if mode == "outside":
        target = tmp_path / "outside/pws-rule.tf"
    elif mode == "existing":
        target.parent.mkdir(parents=True)
        target.write_bytes(b"keep this existing target")
    elif mode == "changed":
        source.write_bytes(b"changed source bytes")
    elif mode == "same_size_mtime":
        content = source.read_bytes()
        source.write_bytes(bytes((content[0] ^ 1,)) + content[1:])
        os.utime(source, ns=(prepared.modified_ns, prepared.modified_ns + 1_000_000))
    elif mode == "extra_link":
        os.link(source, root / "unexpected-link")
    elif mode == "unsupported":

        def unavailable(*_args: Any, **_kwargs: Any) -> None:
            raise OSError("model unsupported filesystem")

        monkeypatch.setattr(os, "link", unavailable)
    elif mode == "copied":
        monkeypatch.setattr(
            os,
            "link",
            lambda original, alias, **_kwargs: Path(alias).write_bytes(Path(original).read_bytes()),
        )
    elif mode == "redirected_parent":
        actual = Path.lstat

        def redirected(path: Path) -> Any:
            if path == root / "path-rule-shadow":
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            return actual(path)

        monkeypatch.setattr(Path, "lstat", redirected)
    with pytest.raises(dev.SecretScanError) as failure:
        dev._link_mirror_alias(root, prepared, target, "model alias", budget)
    assert failure.value.code == "SECRET_SCAN_INPUT_INVALID"
    if mode == "existing":
        assert target.read_bytes() == b"keep this existing target"
    if mode == "outside":
        assert not target.parent.exists()


def test_private_alias_cleanup_after_shadow_scan_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = b"synthetic cleanup payload"
    entry = dev._PathBlobEntry(Path("node_modules/proof.tf"), "1" * 40, len(body))
    monkeypatch.setattr(dev, "_read_git_blob", lambda *_args: body)
    _model_object_reads(monkeypatch)
    paths: list[Path] = []

    def inspect(
        _tool: str,
        _ignore: Path,
        mirror: Path,
        _target: Path,
        label: str,
        _budget: dev._SecretScanEnumerationBudget,
    ) -> None:
        paths.extend(path for path in mirror.rglob("*") if path.is_file())
        if "shadow" in label:
            raise dev.SecretScanError("SECRET_SCAN_FINDING", "model alias-only finding")

    monkeypatch.setattr(dev, "_scan_mirror", inspect)
    with pytest.raises(dev.SecretScanError):
        dev._scan_path_aware_blobs(
            "model",
            "model",
            tmp_path,
            tmp_path / "ignore",
            [entry],
            category="cleanup",
            budget=dev._SecretScanEnumerationBudget(),
        )
    assert len(paths) == 2 and all(not path.exists() for path in paths)


def test_untracked_reuse_keeps_all_three_live_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    ignore, _roots, _payloads = _snapshot(root, monkeypatch, untracked=True)
    actual = dev._read_scan_input
    reads: list[Path] = []

    def read(repository: Path, relative: Path, **kwargs: Any) -> bytes | None:
        reads.append(relative)
        return actual(repository, relative, **kwargs)

    monkeypatch.setattr(dev, "_read_scan_input", read)
    dev._scan_repository_checked(root, ignore, dev._SecretScanEnumerationBudget())
    assert reads.count(Path("baseline.txt")) == 3


def test_index_capture_also_supplies_ordered_tracked_membership_without_second_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    ignore, _roots, _payloads = _snapshot(root, monkeypatch)

    def untracked_only(_git: str, _root: Path, args: tuple[str, ...], _budget: Any) -> list[Path]:
        assert "--cached" not in args, "Validated index rows already contain every cached path"
        return []

    monkeypatch.setattr(dev, "_git_paths", untracked_only)
    dev._scan_repository_checked(root, ignore, dev._SecretScanEnumerationBudget())
    summary = dev.inspect_repository_contents(root, lambda _content: None)
    assert summary.tracked_count == 2


def test_index_only_object_change_is_rejected_even_with_same_paths_and_working_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    ignore, _roots, _payloads = _snapshot(root, monkeypatch)
    initial = dev._index_entries("model", root, dev._SecretScanEnumerationBudget())
    calls = 0

    def index(_git: str, _root: Path, _budget: Any) -> list[dev._PathBlobEntry]:
        nonlocal calls
        calls += 1
        return initial if calls == 1 else [initial[0], replace(initial[1], object_id="4" * 40)]

    monkeypatch.setattr(dev, "_index_entries", index)
    with pytest.raises(dev.SecretScanError) as failure:
        dev._scan_repository_checked(root, ignore, dev._SecretScanEnumerationBudget())
    assert failure.value.code == "SECRET_SCAN_INPUT_INVALID"


def test_missing_tracked_sparse_working_file_does_not_remove_index_or_history_inspection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    _ignore, _roots, _payloads = _snapshot(root, monkeypatch)
    (root / "baseline.txt").unlink()
    inspected: list[bytes] = []
    summary = dev.inspect_repository_contents(root, inspected.append)
    assert summary.index_blob_count == summary.history_blob_count == 2
    assert b"baseline.txt" in inspected and b"synthetic complete source bytes\n" in inspected


@pytest.mark.parametrize(
    "mode", ["middle_mutation", "membership_change", "renamed_alias", "changed_secret"]
)
def test_untracked_reuse_does_not_hide_new_rules_bytes_or_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    root = tmp_path / "repository"
    ignore, _roots, _payloads = _snapshot(root, monkeypatch, untracked=True)
    if mode == "middle_mutation":
        monkeypatch.setattr(
            dev,
            "_scan_mirror",
            lambda *_args: (root / "baseline.txt").write_bytes(b"changed after initial capture\n"),
        )
    elif mode == "membership_change":
        calls = 0
        initial = dev._index_entries("model", root, dev._SecretScanEnumerationBudget())
        historical = dev._historical_path_entries(
            "model", root, _roots, dev._SecretScanEnumerationBudget()
        )

        def index(_git: str, _root: Path, _budget: Any) -> list[dev._PathBlobEntry]:
            nonlocal calls
            calls += 1
            return initial if calls == 1 else [*initial, historical[1]]

        monkeypatch.setattr(dev, "_index_entries", index)
    elif mode == "renamed_alias":
        (root / "renamed.tf").write_bytes((root / "baseline.txt").read_bytes())
        monkeypatch.setattr(
            dev,
            "_git_paths",
            lambda _git, _root, args, _budget: (
                [Path(".gitleaks.toml")] if "--cached" in args else [Path("renamed.tf")]
            ),
        )

        def detect(
            _tool: str, _ignore: Path, _mirror: Path, _target: Path, label: str, _budget: Any
        ) -> None:
            if label.startswith("untracked source shadow"):
                raise dev.SecretScanError("SECRET_SCAN_FINDING", "model filename-only finding")

        monkeypatch.setattr(dev, "_scan_mirror", detect)
    else:
        (root / "baseline.txt").write_bytes(b"SYNTHETIC_SECOND_GROUP_SECRET\n")
    with pytest.raises(dev.SecretScanError) as failure:
        dev._scan_repository_checked(root, ignore, dev._SecretScanEnumerationBudget())
    expected = (
        "SECRET_SCAN_INPUT_INVALID"
        if mode in {"middle_mutation", "membership_change"}
        else "SECRET_SCAN_FINDING"
    )
    assert failure.value.code == expected


@pytest.mark.parametrize("mode", ["direct_blob", "staged_different"])
def test_new_staged_and_direct_ref_bodies_still_scan_when_current_bytes_match_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    root = tmp_path / "repository"
    ignore, roots, _payloads = _snapshot(root, monkeypatch)
    secret = b"SYNTHETIC_SECOND_GROUP_SECRET\n"
    original_read = dev._read_git_blob
    historical = dev._historical_objects(
        "model", root, roots, [], dev._SecretScanEnumerationBudget()
    )

    def read(
        git: str,
        repository: Path,
        oid: str,
        size: int,
        label: str,
        budget: dev._SecretScanEnumerationBudget,
    ) -> bytes:
        if oid == "4" * 40:
            budget.consume_work(len(secret))
            return secret
        return original_read(git, repository, oid, size, label, budget)

    monkeypatch.setattr(dev, "_read_git_blob", read)
    if mode == "direct_blob":
        changed = replace(
            roots, ref_names=(*roots.ref_names, b"refs/pws/direct-blob"), blob_ids=("4" * 40,)
        )
        monkeypatch.setattr(dev, "_history_roots", lambda *_args: changed)
        monkeypatch.setattr(
            dev,
            "_historical_objects",
            lambda *_args: ([*historical[0], ("4" * 40, len(secret))], historical[1]),
        )
    else:
        original_index = dev._index_entries("model", root, dev._SecretScanEnumerationBudget())
        monkeypatch.setattr(
            dev,
            "_index_entries",
            lambda *_args: [
                original_index[0],
                dev._PathBlobEntry(Path("baseline.txt"), "4" * 40, len(secret)),
            ],
        )
    with pytest.raises(dev.SecretScanError) as failure:
        dev._scan_repository_checked(root, ignore, dev._SecretScanEnumerationBudget())
    assert failure.value.code == "SECRET_SCAN_FINDING"

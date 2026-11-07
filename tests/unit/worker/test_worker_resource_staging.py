"""Failure-path coverage for Windows live-worker test-resource staging."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import AbstractContextManager
from pathlib import Path

import pytest

from tests import worker_resources

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="PWS-0107 resource staging targets supported Windows hosts"
)


def test_runtime_audit_failure_removes_random_fixed_drive_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage_root = worker_resources.create_worker_test_stage_root()

    def fail_audit(_root: Path, _budget: object) -> tuple[Path, ...]:
        raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr(worker_resources, "_audit_runtime_source", fail_audit)
    try:
        with pytest.raises(RuntimeError, match="synthetic audit failure"):
            worker_resources.stage_worker_test_resources(stage_root)
        assert not os.path.lexists(stage_root)
    finally:
        if os.path.lexists(stage_root):
            worker_resources._remove_stage_root(stage_root)


def test_pillow_stage_source_requires_the_exact_admitted_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root = worker_resources._pillow_package_source()
    assert package_root.name == "PIL"
    assert (package_root / "__init__.py").is_file()

    monkeypatch.setattr("tests.worker_resources.PIL.__version__", "12.3.1")
    with pytest.raises(RuntimeError, match="admitted Windows Pillow test runtime"):
        worker_resources._pillow_package_source()


def test_unconfirmed_cleanup_guard_preserves_exact_resources_and_refuses_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    sentinel = stage / "retained-pinned-object.bin"
    sentinel.write_bytes(b"exact ACL recovery identity")
    resources = worker_resources.WorkerTestResources(stage, stage / "runtime", stage / "code")
    removed: list[Path] = []
    monkeypatch.setattr(worker_resources, "_remove_stage_root", removed.append)
    resources.cleanup_guard.cleanup_safe = False

    with pytest.raises(RuntimeError, match="exact staged resources were retained"):
        worker_resources.cleanup_worker_test_resources(resources)

    assert removed == []
    assert resources.stage_root == stage
    assert sentinel.read_bytes() == b"exact ACL recovery identity"


def test_confirmed_cleanup_guard_delegates_to_existing_validated_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = worker_resources.WorkerTestResources(
        tmp_path / "stage", tmp_path / "runtime", tmp_path / "code"
    )
    removed: list[Path] = []
    monkeypatch.setattr(worker_resources, "_remove_stage_root", removed.append)
    worker_resources.cleanup_worker_test_resources(resources)
    assert removed == [resources.stage_root]


def _resources(tmp_path: Path) -> worker_resources.WorkerTestResources:
    stage = tmp_path / "durable-owned-stage"
    stage.mkdir()
    return worker_resources.WorkerTestResources(stage, stage / "runtime", stage / "repository")


def test_private_journal_and_extra_granted_objects_are_durable_siblings_outside_code(
    tmp_path: Path,
) -> None:
    resources = _resources(tmp_path)
    journal = worker_resources.create_worker_test_recovery_path(resources, "native-proof")
    code = worker_resources.create_worker_test_owned_directory(resources, "extra-code")
    assert journal.parent == resources.recovery_root
    assert not journal.exists()  # Production creates the leaf's protected ACL.
    assert code.is_dir() and code.is_relative_to(resources.stage_root)
    assert not journal.is_relative_to(resources.source_root)
    assert not journal.is_relative_to(resources.runtime_root)
    assert not journal.is_relative_to(code)


def test_nonempty_or_unreadable_journal_preserves_stage_even_without_fixture_failure_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = _resources(tmp_path)
    journal = worker_resources.create_worker_test_recovery_path(resources, "native-proof")
    journal.mkdir()
    record = journal / "synthetic-unreadable-record.json"
    record.write_bytes(b"keep exact synthetic ledger identity")
    removed: list[Path] = []
    monkeypatch.setattr(worker_resources, "_remove_stage_root", removed.append)
    initial_safe = resources.cleanup_guard.cleanup_safe
    assert initial_safe
    with pytest.raises(RuntimeError, match="exact staged resources were retained"):
        worker_resources.cleanup_worker_test_resources(resources)
    assert not resources.cleanup_guard.cleanup_safe and removed == []
    assert record.read_bytes() == b"keep exact synthetic ledger identity"


def test_recovery_scan_refusal_retains_stage_without_parsing_or_deleting_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = _resources(tmp_path)
    resources.recovery_root.mkdir()
    original = os.scandir

    def denied(
        path: str | os.PathLike[str] | int,
    ) -> AbstractContextManager[Iterator[os.DirEntry[str]]]:
        if path == resources.recovery_root:
            raise PermissionError("synthetic inaccessible recovery directory")
        return original(path)

    monkeypatch.setattr(os, "scandir", denied)
    with pytest.raises(RuntimeError, match="exact staged resources were retained"):
        worker_resources.cleanup_worker_test_resources(resources)
    assert not resources.cleanup_guard.cleanup_safe and resources.stage_root.exists()


def test_recovery_directory_inventory_limit_fails_closed_before_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = _resources(tmp_path)
    resources.recovery_root.mkdir()
    (resources.recovery_root / "one").mkdir()
    (resources.recovery_root / "two").mkdir()
    monkeypatch.setattr(worker_resources, "_MAX_TEST_RECOVERY_JOURNALS", 1)
    with pytest.raises(RuntimeError, match="exact staged resources were retained"):
        worker_resources.cleanup_worker_test_resources(resources)
    assert not resources.cleanup_guard.cleanup_safe and resources.stage_root.exists()


def test_confirmed_empty_durable_journal_allows_existing_safe_stage_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = _resources(tmp_path)
    worker_resources.create_worker_test_recovery_path(resources, "native-proof").mkdir()
    removed: list[Path] = []
    monkeypatch.setattr(worker_resources, "_remove_stage_root", removed.append)
    worker_resources.cleanup_worker_test_resources(resources)
    assert removed == [resources.stage_root]

"""Shared deterministic pytest fixtures."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from tests.worker_resources import (
    WorkerTestResources,
    cleanup_worker_test_resources,
    create_worker_test_stage_root,
    stage_worker_test_resources,
)


def pytest_addoption(parser: pytest.Parser) -> None:
    """Select an explicit revision-bound quality receipt for a frozen candidate."""

    parser.addoption(
        "--pws0207-baseline",
        action="store",
        default=None,
        help=(
            "Use a newly recorded PWS-0207 baseline for this exact frozen source; "
            "the complete corpus, index and raw-source guards still apply."
        ),
    )


@pytest.fixture(autouse=True)
def dispatch_scheduled_qt_deletions() -> Iterator[None]:
    """Complete UI fixture deleteLater calls as the real application loop would.

    processEvents alone does not deliver DeferredDelete outside app.exec(). A
    pending-save dialog can reject close(), so leaving its scheduled deletion
    undelivered lets an obsolete modal suppress shortcuts in the next test.
    This only destroys objects the owning fixture already scheduled for deletion.
    """
    yield
    if QCoreApplication.instance() is not None:
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture(scope="session")
def worker_test_resources() -> Iterator[WorkerTestResources]:
    """Provide physical resource trees for live Windows containment tests."""

    resources = stage_worker_test_resources(create_worker_test_stage_root())
    try:
        yield resources
    finally:
        cleanup_worker_test_resources(resources)


@dataclass(slots=True)
class OwnedWorkerCodeSnapshot:
    """Exact trusted test code, retained if worker cleanup remains unconfirmed."""

    root: Path
    inventory_json: str
    cleanup_safe: bool = True

    @property
    def recovery_root(self) -> Path:
        return self.root.parent.parent / "private-recovery"


def _owned_worker_recovery_is_empty(root: Path) -> bool:
    """Retain records with their exact objects, outside pytest's retention tree."""

    if not os.path.lexists(root):
        return True
    try:
        pending = [root]
        visited = 0
        while pending:
            current = pending.pop()
            observed = current.lstat()
            if (
                not stat.S_ISDIR(observed.st_mode)
                or getattr(observed, "st_file_attributes", 0) & 0x400
            ):
                return False
            with os.scandir(current) as entries:
                for entry in entries:
                    visited += 1
                    if visited > 256 or not entry.is_dir(follow_symlinks=False):
                        return False
                    pending.append(Path(entry.path))
    except OSError:
        return False
    return True


@pytest.fixture(scope="session")
def owned_worker_code_snapshot(request: pytest.FixtureRequest) -> OwnedWorkerCodeSnapshot:
    """Freeze one byte-identical code tree owned by the executing test user.

    A source checkout can contain files owned by an earlier sandbox account. A
    Modify grant permits source edits but deliberately does not permit WRITE_DAC.
    Preserve that boundary: copy trusted code bytes rather than changing owners,
    broadening ACLs, or weakening the production launcher's pinned-object checks.
    """

    repository = Path(__file__).resolve().parents[1]
    staging_parent = repository / ".paperworkshell-local"
    staging_parent.mkdir(exist_ok=True)
    tmp_path = Path(tempfile.mkdtemp(prefix="pws-worker-code-", dir=staging_parent)).resolve()
    if not tmp_path.is_relative_to(staging_parent.resolve()):
        raise RuntimeError("Worker test code staging escaped its workspace.")
    source = repository / "src"
    target = tmp_path / "owned-production-code" / "src"
    snapshot = OwnedWorkerCodeSnapshot(target, "")

    def cleanup() -> None:
        if not _owned_worker_recovery_is_empty(snapshot.recovery_root):
            snapshot.cleanup_safe = False
        if snapshot.cleanup_safe:
            if not tmp_path.resolve().is_relative_to(staging_parent.resolve()):
                raise RuntimeError("Worker test staging cleanup escaped its workspace.")
            shutil.rmtree(tmp_path)

    request.addfinalizer(cleanup)
    target.mkdir(parents=True)
    inventory: list[dict[str, object]] = []
    total_bytes = 0
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if "__pycache__" in relative.parts or path.suffix.lower() in {".pyc", ".pyo"}:
            continue
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise RuntimeError("Worker test code must not contain redirected resources.")
        if path.is_dir():
            (target / relative).mkdir(parents=True, exist_ok=True)
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError("Worker test code must contain regular files only.")
        data = path.read_bytes()
        total_bytes += len(data)
        if len(inventory) >= 20_000 or total_bytes > 512 * 1024 * 1024:
            raise RuntimeError("Worker test code exceeds its staging budget.")
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
            raise RuntimeError("Worker test code copy did not preserve source bytes.")
        inventory.append({"path": relative.as_posix(), "bytes": len(data), "sha256": digest})
    for item in inventory:
        if hashlib.sha256((source / str(item["path"])).read_bytes()).hexdigest() != item["sha256"]:
            raise RuntimeError(
                "Worker test source changed while preparing its exact snapshot: "
                + str(item["path"])
            )
    encoded = json.dumps(inventory, sort_keys=True, separators=(",", ":"))
    (target.parent / "source-inventory.json").write_text(encoded, encoding="utf-8")
    snapshot.inventory_json = encoded
    return snapshot


@pytest.fixture
def owned_production_worker_code(
    owned_worker_code_snapshot: OwnedWorkerCodeSnapshot,
    monkeypatch: pytest.MonkeyPatch,
    record_property: Callable[[str, object], None],
) -> Iterator[Path]:
    """Use exact staged code with the actual production worker configuration."""

    import paperworkshell.__main__ as desktop

    target = owned_worker_code_snapshot.root
    encoded = owned_worker_code_snapshot.inventory_json
    record_property("owned_worker_code_inventory", encoded)
    record_property(
        "owned_worker_code_inventory_sha256", hashlib.sha256(encoded.encode()).hexdigest()
    )
    executable, venv, _bootstrap, runtime_roots, _code_roots, _working = (
        desktop._worker_runtime_resources()
    )
    selected_runtime = os.environ.get("PWS_TEST_RUNTIME_ROOT")
    if selected_runtime:
        runtime = Path(selected_runtime).resolve(strict=True)
        marker = runtime / "paperworkshell-installed-v1.json"
        identity = json.loads(marker.read_bytes())
        executable = runtime / "python.exe"
        if (
            not executable.is_file()
            or identity.get("application_id") != "io.github.pikkujanne.paperworkshell"
        ):
            raise RuntimeError("Worker test runtime is not an identified PaperWorkShell runtime.")
        venv = None
        runtime_roots = (runtime,)
        runtime_inventory: list[dict[str, object]] = []
        for resource in sorted(runtime.rglob("*")):
            if "__pycache__" in resource.parts or resource.suffix.lower() in {".pyc", ".pyo"}:
                continue
            if resource.is_file():
                with resource.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                runtime_inventory.append(
                    {
                        "path": resource.relative_to(runtime).as_posix(),
                        "bytes": resource.stat().st_size,
                        "sha256": digest,
                    }
                )
        runtime_encoded = json.dumps(runtime_inventory, sort_keys=True, separators=(",", ":"))
        record_property("worker_runtime_candidate", identity.get("candidate_id"))
        record_property("worker_runtime_inventory", runtime_encoded)
        record_property(
            "worker_runtime_inventory_sha256", hashlib.sha256(runtime_encoded.encode()).hexdigest()
        )
    bootstrap = target / "paperworkshell" / "worker" / "host_bootstrap.py"
    monkeypatch.setattr(
        desktop,
        "_worker_runtime_resources",
        lambda: (executable, venv, bootstrap, runtime_roots, (target,), target),
    )
    from paperworkshell.worker.client import _windows as windows

    recovery_parent = owned_worker_code_snapshot.recovery_root
    recovery_parent.mkdir(exist_ok=True)
    recovery = recovery_parent / os.urandom(12).hex()
    monkeypatch.setattr(windows, "_acl_recovery_directory_path", lambda: recovery)
    record_property(
        "worker_recovery_scope",
        recovery.relative_to(Path(__file__).resolve().parents[1]).as_posix(),
    )
    yield target
    if not _owned_worker_recovery_is_empty(recovery_parent):
        owned_worker_code_snapshot.cleanup_safe = False
        raise RuntimeError(
            "Worker cleanup was not confirmed; exact staged code was retained for recovery."
        )

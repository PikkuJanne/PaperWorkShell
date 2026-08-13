"""Physical resource staging for live Windows worker-containment tests."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import PIL

from paperworkshell.worker.client._windows import _approved_resource_tree

_REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]
_MAX_RESOURCE_ENTRIES: Final = 20_000
_MAX_RESOURCE_DEPTH: Final = 64
_MAX_RESOURCE_BYTES: Final = 512 * 1024 * 1024
_MAX_SINGLE_FILE_BYTES: Final = 128 * 1024 * 1024
_REPARSE_POINT_ATTRIBUTE: Final = 0x0000_0400
_RUNTIME_DIRECTORIES: Final = ("DLLs", "Lib")
_RUNTIME_IGNORED_DIRECTORIES: Final = frozenset({"site-packages"})
_STAGE_PREFIX: Final = "pws-worker-resources-"
_EXPECTED_PILLOW_VERSION: Final = "12.3.0"


@dataclass(frozen=True, slots=True)
class WorkerTestResources:
    """A temporary, non-reparse runtime and source layout for a live worker."""

    stage_root: Path
    runtime_root: Path
    repository_root: Path

    @property
    def python_executable(self) -> Path:
        return self.runtime_root / "python.exe"

    @property
    def source_root(self) -> Path:
        return self.repository_root / "src"

    @property
    def helper_root(self) -> Path:
        return self.repository_root / "tests" / "helpers"


@dataclass(slots=True)
class _CopyBudget:
    entries: int = 0
    total_bytes: int = 0

    def observe_directory(self) -> None:
        self.entries += 1
        if self.entries > _MAX_RESOURCE_ENTRIES:
            raise RuntimeError("a Windows worker test-resource source has too many entries")

    def observe_file(self, path: Path) -> None:
        try:
            size = path.lstat().st_size
        except OSError:
            raise RuntimeError(
                "a Windows worker test-resource source cannot be inspected"
            ) from None
        if size < 0 or size > _MAX_SINGLE_FILE_BYTES:
            raise RuntimeError("a Windows worker test-resource file is too large")
        self.entries += 1
        self.total_bytes += size
        if self.entries > _MAX_RESOURCE_ENTRIES:
            raise RuntimeError("a Windows worker test-resource source has too many entries")
        if self.total_bytes > _MAX_RESOURCE_BYTES:
            raise RuntimeError("Windows worker test resources are too large")


def create_worker_test_stage_root() -> Path:
    """Create one random staging directory directly under CPython's fixed drive."""

    fixed_drive_root = Path(Path(sys.base_prefix).anchor)
    return Path(tempfile.mkdtemp(prefix=_STAGE_PREFIX, dir=fixed_drive_root))


def stage_worker_test_resources(stage_root: Path) -> WorkerTestResources:
    """Copy trusted test resources and remove the stage after every failure."""

    try:
        return _stage_worker_test_resources(stage_root)
    except BaseException:
        if os.path.lexists(stage_root):
            try:
                _remove_stage_root(stage_root)
            except OSError, RuntimeError:
                raise RuntimeError("Windows worker test-resource cleanup failed") from None
        raise


def _stage_worker_test_resources(stage_root: Path) -> WorkerTestResources:
    """Copy trusted test resources away from hosted-runner reparse paths."""

    if os.name != "nt":
        raise RuntimeError("Windows worker resources may only be staged on Windows")

    runtime_source = Path(sys.base_prefix)
    pillow_source = _pillow_package_source()
    source_source = _REPOSITORY_ROOT / "src"
    helper_source = _REPOSITORY_ROOT / "tests" / "helpers"
    budget = _CopyBudget()
    runtime_files = _audit_runtime_source(runtime_source, budget)
    runtime_directories = tuple(runtime_source / name for name in _RUNTIME_DIRECTORIES)
    for directory in runtime_directories:
        _audit_copy_source(
            directory,
            budget,
            ignored_directory_names=(
                _RUNTIME_IGNORED_DIRECTORIES if directory.name.casefold() == "lib" else frozenset()
            ),
        )
    _audit_copy_source(pillow_source, budget)
    _audit_copy_source(source_source, budget)
    _audit_copy_source(helper_source, budget)

    runtime_root = stage_root / "runtime"
    repository_root = stage_root / "repository"
    source_root = repository_root / "src"
    helper_root = repository_root / "tests" / "helpers"
    try:
        runtime_root.mkdir()
        for source in runtime_files:
            shutil.copyfile(source, runtime_root / source.name)
        for source in runtime_directories:
            shutil.copytree(
                source,
                runtime_root / source.name,
                ignore=_copy_ignore(
                    _RUNTIME_IGNORED_DIRECTORIES if source.name.casefold() == "lib" else frozenset()
                ),
                copy_function=shutil.copyfile,
            )
        pillow_destination = runtime_root / "Lib" / "site-packages" / "PIL"
        pillow_destination.parent.mkdir(exist_ok=True)
        shutil.copytree(
            pillow_source,
            pillow_destination,
            ignore=_copy_ignore(frozenset()),
            copy_function=shutil.copyfile,
        )
        shutil.copytree(
            source_source,
            source_root,
            ignore=_copy_ignore(frozenset()),
            copy_function=shutil.copyfile,
        )
        shutil.copytree(
            helper_source,
            helper_root,
            ignore=_copy_ignore(frozenset()),
            copy_function=shutil.copyfile,
        )
        (repository_root / "pyproject.toml").write_bytes(
            b"[project]\nname = 'pws-containment-denial-canary'\n"
        )
        resources = WorkerTestResources(stage_root, runtime_root, repository_root)
        if not resources.python_executable.is_file():
            raise RuntimeError("the staged Windows Python executable is unavailable")
        for root in (resources.runtime_root, resources.source_root, resources.helper_root):
            _approved_resource_tree(root)
        return resources
    except BaseException:
        try:
            _remove_stage_root(stage_root)
        except OSError:
            raise RuntimeError("Windows worker test-resource cleanup failed") from None
        raise


def _pillow_package_source() -> Path:
    """Return the exact admitted Pillow package tree before staging begins."""

    if PIL.__version__ != _EXPECTED_PILLOW_VERSION:
        raise RuntimeError("the admitted Windows Pillow test runtime is unavailable")
    package_file = getattr(PIL, "__file__", None)
    if not isinstance(package_file, str) or not package_file:
        raise RuntimeError("the admitted Windows Pillow test runtime is unavailable")
    package_root = Path(package_file).parent
    if not package_root.is_absolute() or package_root.name != "PIL":
        raise RuntimeError("the admitted Windows Pillow test runtime is unavailable")
    return package_root


def cleanup_worker_test_resources(resources: WorkerTestResources) -> None:
    """Remove exactly the validated random fixed-drive staging directory."""

    _remove_stage_root(resources.stage_root)


def _remove_stage_root(stage_root: Path) -> None:
    fixed_drive_root = Path(Path(sys.base_prefix).anchor).resolve(strict=True)
    try:
        resolved = stage_root.resolve(strict=True)
    except OSError:
        raise RuntimeError("the Windows worker test-resource stage is unavailable") from None
    if (
        resolved.parent != fixed_drive_root
        or not resolved.name.startswith(_STAGE_PREFIX)
        or len(resolved.name) <= len(_STAGE_PREFIX)
        or not resolved.is_dir()
        or _is_reparse_point(resolved)
    ):
        raise RuntimeError("the Windows worker test-resource cleanup target is invalid")
    shutil.rmtree(resolved)


def _audit_runtime_source(root: Path, budget: _CopyBudget) -> tuple[Path, ...]:
    """Select the fixed executable and DLL files required beside CPython."""

    if not root.is_absolute() or not root.is_dir():
        raise RuntimeError("the Windows Python test runtime is unavailable")
    budget.observe_directory()
    selected: list[Path] = []
    try:
        children = sorted(root.iterdir(), key=lambda child: child.name.casefold())
    except OSError:
        raise RuntimeError("the Windows Python test runtime cannot be read") from None
    for child in children:
        if _is_ignored_cache_entry(child):
            continue
        if _is_reparse_point(child):
            if _is_expected_python_alias(child, root):
                continue
            raise RuntimeError("the Windows Python test runtime contains a redirect")
        if child.is_dir():
            continue
        if not child.is_file():
            raise RuntimeError("the Windows Python test runtime contains a special entry")
        budget.observe_file(child)
        selected.append(child)
    if not all((root / name).is_dir() for name in _RUNTIME_DIRECTORIES):
        raise RuntimeError("the Windows Python test runtime is incomplete")
    return tuple(selected)


def _audit_copy_source(
    root: Path,
    budget: _CopyBudget,
    *,
    ignored_directory_names: frozenset[str] = frozenset(),
) -> None:
    """Reject redirects or special files that a recursive copy could traverse."""

    if not root.is_absolute() or not root.is_dir():
        raise RuntimeError("a Windows worker test-resource source is unavailable")
    pending: list[tuple[Path, int]] = [(root, 0)]
    while pending:
        current, depth = pending.pop()
        if depth > _MAX_RESOURCE_DEPTH:
            raise RuntimeError("a Windows worker test-resource source is too deep")
        if _is_reparse_point(current):
            raise RuntimeError("a Windows worker test-resource source contains a redirect")
        if current.is_file():
            budget.observe_file(current)
            continue
        if not current.is_dir():
            raise RuntimeError("a Windows worker test-resource source contains a special entry")
        budget.observe_directory()
        try:
            children = sorted(current.iterdir(), key=lambda child: child.name.casefold())
        except OSError:
            raise RuntimeError("a Windows worker test-resource source cannot be read") from None
        pending.extend(
            (child, depth + 1)
            for child in reversed(children)
            if not _is_ignored_cache_entry(child)
            and child.name.casefold() not in ignored_directory_names
        )


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = int(getattr(path.lstat(), "st_file_attributes", 0))
    except OSError:
        raise RuntimeError("a Windows worker test-resource source cannot be inspected") from None
    return bool(attributes & _REPARSE_POINT_ATTRIBUTE)


def _is_expected_python_alias(path: Path, runtime_root: Path) -> bool:
    if path.parent != runtime_root or path.name.casefold() != "python3.exe":
        return False
    executable = runtime_root / "python.exe"
    try:
        return executable.is_file() and path.resolve(strict=True) == executable.resolve(strict=True)
    except OSError:
        return False


def _is_ignored_cache_entry(path: Path) -> bool:
    return path.name == "__pycache__" or path.suffix.casefold() in {".pyc", ".pyo"}


def _copy_ignore(
    ignored_directory_names: frozenset[str],
) -> Callable[[str, list[str]], set[str]]:
    def ignore(directory: str, names: list[str]) -> set[str]:
        parent = Path(directory)
        return {
            name
            for name in names
            if _is_ignored_cache_entry(parent / name) or name.casefold() in ignored_directory_names
        }

    return ignore

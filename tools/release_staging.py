"""Explicit Windows dependency staging and exhaustive offline file reconciliation."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final, cast

from tools.audit_tesseract_redistribution import pe_imports
from tools.release_artifacts import (
    MAX_STAGE_BYTES,
    Artifact,
    ReleaseArtifactError,
    extract_selected_zip,
    file_hashes,
    plain_path,
    safe_relative,
    verify_artifact,
)

MAX_STAGE_FILES: Final = 25_000
_QT_BINDINGS: Final = ("QtCore.pyd", "QtGui.pyd", "QtWidgets.pyd")
_QT_PLUGINS: Final = (
    "PySide6/plugins/platforms/qwindows.dll",
    "PySide6/plugins/styles/qmodernwindowsstyle.dll",
)


def _native_dependencies(archive: zipfile.ZipFile, selected: set[str]) -> set[str]:
    """Resolve normal/delay PE imports present in this exact wheel, never OS files."""
    dlls: dict[str, str] = {}
    for name in archive.namelist():
        if name.casefold().endswith(".dll"):
            key = Path(name).name.casefold()
            if key in dlls:
                raise ReleaseArtifactError("RELEASE_COLLISION")
            dlls[key] = name
    pending = sorted(name for name in selected if name.endswith((".dll", ".pyd")))
    inspected: set[str] = set()
    while pending:
        name = pending.pop()
        if name in inspected:
            continue
        inspected.add(name)
        info = archive.getinfo(name)
        if info.file_size > 32 * 1024 * 1024 or len(inspected) > 256:
            raise ReleaseArtifactError("RELEASE_NATIVE_LIMIT")
        normal, delayed = pe_imports(archive.read(info))
        for dependency in (*normal, *delayed):
            item = dlls.get(dependency)
            if item is not None and item not in selected:
                selected.add(item)
                pending.append(item)
    return selected


def wheel_selection(
    path: Path, expected: Artifact, component_id: str, exclusions: Mapping[str, str]
) -> dict[str, str]:
    """Select regular package content, omit installer scripts and stale wheel RECORD.

    PySide/shiboken receive an explicit dynamically replaceable Widgets subset;
    remaining wheels keep their package content. Every omission is returned by the
    caller's separate reconciliation record rather than hidden in a forged RECORD.
    """
    verify_artifact(path, expected)
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) > MAX_STAGE_FILES:
            raise ReleaseArtifactError("RELEASE_LIMIT")
        files = {
            name
            for name in names
            if not name.endswith("/")
            and (".data/" not in name or ".data/platlib/" in name or ".data/purelib/" in name)
            and not name.endswith(".dist-info/RECORD")
            # Vendor SBOMs can contain upstream CI machine paths. Retain them as
            # authenticated review inputs; publish the normalized candidate BOMs.
            and "/sboms/" not in name
            and "/__pycache__/" not in name
        }
        for name, digest in exclusions.items():
            if name not in files or hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise ReleaseArtifactError("RELEASE_EXCLUSION")
            files.remove(name)
        if component_id == "pyside6-essentials":
            files = {
                name
                for name in files
                if ".dist-info/" in name
                or (name.startswith("PySide6/") and name.count("/") == 1 and name.endswith(".py"))
                or name in _QT_PLUGINS
                or name in {"PySide6/" + binding for binding in _QT_BINDINGS}
                or name in {"PySide6/support/__init__.py", "PySide6/support/deprecated.py"}
            }
            if not {"PySide6/" + binding for binding in _QT_BINDINGS} <= files:
                raise ReleaseArtifactError("RELEASE_QT_SUBSET")
            files = _native_dependencies(archive, files)
            forbidden = ("Qt6Network", "Qt6Qml", "Qt6Quick", "Qt6Pdf", "Qt6Designer")
            if any(any(part in name for part in forbidden) for name in files):
                raise ReleaseArtifactError("RELEASE_QT_SUBSET")
            if "PySide6/plugins/platforms/qwindows.dll" not in files:
                raise ReleaseArtifactError("RELEASE_QT_SUBSET")
        elif component_id == "shiboken6":
            files = {
                name
                for name in files
                if ".dist-info/" in name
                or name in {"shiboken6/Shiboken.pyd", "shiboken6/shiboken6.abi3.dll"}
                or (name.startswith("shiboken6/") and name.count("/") == 1 and name.endswith(".py"))
            }
            files = _native_dependencies(archive, files)
        selected: dict[str, str] = {}
        for name in sorted(files):
            relocated = name
            for scheme in (".data/platlib/", ".data/purelib/"):
                if scheme in name:
                    relocated = name.split(scheme, 1)[1]
                    break
            selected[name] = "runtime/Lib/site-packages/" + safe_relative(relocated)
        return selected


def stage_wheel(
    path: Path,
    expected: Artifact,
    destination: Path,
    component_id: str,
    *,
    exclusions: Mapping[str, str] | None = None,
) -> tuple[list[str], list[str]]:
    selection = wheel_selection(path, expected, component_id, exclusions or {})
    with zipfile.ZipFile(path) as archive:
        omitted = sorted(
            name for name in archive.namelist() if not name.endswith("/") and name not in selection
        )
    return extract_selected_zip(path, expected, destination, selection), omitted


def inventory_files(stage: Path, owners: Mapping[str, str]) -> list[dict[str, object]]:
    """Require an exhaustive case-safe ownership map and hash every actual file."""
    plain_path(stage)
    actual: set[str] = set()
    results: list[dict[str, object]] = []
    total = 0
    pending = [stage]
    paths: list[Path] = []
    entry_count = 0
    while pending:
        directory = pending.pop()
        plain_path(directory)
        with os.scandir(directory) as entries:
            for entry in entries:
                entry_count += 1
                if entry_count > MAX_STAGE_FILES * 2:
                    raise ReleaseArtifactError("RELEASE_LIMIT")
                path = Path(entry.path)
                plain_path(path)
                if len(path.relative_to(stage).parts) > 64:
                    raise ReleaseArtifactError("RELEASE_LIMIT")
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                else:
                    if len(paths) >= MAX_STAGE_FILES:
                        raise ReleaseArtifactError("RELEASE_LIMIT")
                    paths.append(path)
    for path in sorted(paths, key=lambda item: item.relative_to(stage).as_posix()):
        plain_path(path)
        if path.is_dir():
            continue
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ReleaseArtifactError("RELEASE_FILE")
        relative = safe_relative(path.relative_to(stage).as_posix())
        folded = relative.casefold()
        if folded in actual or len(actual) >= MAX_STAGE_FILES:
            raise ReleaseArtifactError("RELEASE_COLLISION")
        actual.add(folded)
        if relative not in owners:
            raise ReleaseArtifactError("RELEASE_UNOWNED_FILE")
        size, digest, sha1 = file_hashes(path)
        total += size
        if total > MAX_STAGE_BYTES:
            raise ReleaseArtifactError("RELEASE_LIMIT")
        results.append(
            {
                "path": relative,
                "component_id": owners[relative],
                "size_bytes": size,
                "sha256": digest,
                "sha1": sha1,
            }
        )
    if len(actual) != len(owners) or actual != {safe_relative(path).casefold() for path in owners}:
        raise ReleaseArtifactError("RELEASE_MISSING_FILE")
    return results


def verify_files(stage: Path, expected: Sequence[Mapping[str, Any]]) -> None:
    """Missing, extra, changed or aliased files cannot become a passing inventory."""
    owners = {cast(str, item["path"]): cast(str, item["component_id"]) for item in expected}
    if len(owners) != len(expected):
        raise ReleaseArtifactError("RELEASE_COLLISION")
    actual = inventory_files(stage, owners)
    if actual != sorted((dict(item) for item in expected), key=lambda item: item["path"]):
        raise ReleaseArtifactError("RELEASE_STAGED_HASH")


def write_package(
    stage: Path, expected: Sequence[Mapping[str, Any]], output: Path
) -> dict[str, object]:
    """Write one deterministic new stage ZIP, then reopen and verify every entry."""
    verify_files(stage, expected)
    plain_path(output)
    created = False
    try:
        with output.open("xb") as stream:
            created = True
            with zipfile.ZipFile(
                stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
            ) as archive:
                for record in sorted(expected, key=lambda item: item["path"]):
                    name = cast(str, record["path"])
                    info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = (stat.S_IFREG | 0o644) << 16
                    with (
                        archive.open(info, "w", force_zip64=True) as target,
                        (stage / name).open("rb") as source,
                    ):
                        while chunk := source.read(1024 * 1024):
                            target.write(chunk)
        with zipfile.ZipFile(output) as archive:
            records = {cast(str, item["path"]): item for item in expected}
            if set(archive.namelist()) != set(records) or len(archive.infolist()) != len(records):
                raise ReleaseArtifactError("RELEASE_PACKAGE_FILES")
            for name, record in sorted(records.items()):
                digest = hashlib.sha256()
                size = 0
                with archive.open(name) as source:
                    while chunk := source.read(1024 * 1024):
                        size += len(chunk)
                        if size > cast(int, record["size_bytes"]):
                            raise ReleaseArtifactError("RELEASE_LIMIT")
                        digest.update(chunk)
                if size != record["size_bytes"] or digest.hexdigest() != record["sha256"]:
                    raise ReleaseArtifactError("RELEASE_PACKAGE_HASH")
        size, package_digest, _ = file_hashes(output, MAX_STAGE_BYTES)
    except BaseException:
        if created:
            output.unlink(missing_ok=True)
        raise
    return {
        "name": output.name,
        "size_bytes": size,
        "sha256": package_digest,
        "file_count": len(expected),
    }


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")

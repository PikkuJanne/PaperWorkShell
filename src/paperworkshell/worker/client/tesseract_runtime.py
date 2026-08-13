"""Hash-addressed admission for the packaged Windows Tesseract closure.

The dependency inventory is evidence for maintainers.  This module is the
runtime enforcement point: only the byte-identical reviewed manifest and the
exact file closure it describes may become worker launch authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final, Never

TESSERACT_RUNTIME_MANIFEST_SHA256: Final = (
    "1ffb85724770b4c30aa4d982564d548dadf466ebd5861cd827d2836198119c0a"
)
TESSERACT_RUNTIME_FILE_COUNT: Final = 39
TESSERACT_RUNTIME_ENTRYPOINT: Final = PurePosixPath("bin/tesseract.exe")
TESSERACT_RUNTIME_TESSDATA_DIRECTORY: Final = PurePosixPath("share/tessdata")
_TESSERACT_RUNTIME_MANIFEST_NAME: Final = "tesseract-runtime-closure-v1.json"
_MAX_MANIFEST_BYTES: Final = 1_048_576
_MAX_RUNTIME_DEPTH: Final = 16
_MAX_RUNTIME_ENTRIES: Final = 128
_FILE_ATTRIBUTE_REPARSE_POINT: Final = 0x0000_0400


@dataclass(slots=True)
class TesseractRuntimeAdmissionError(RuntimeError):
    """Path-free reason that an OCR runtime was not admitted."""

    context: str

    def __str__(self) -> str:
        return f"Tesseract runtime admission failed at {self.context}."


@dataclass(frozen=True, slots=True)
class TesseractRuntimeAdmission:
    """Authenticated fixed paths safe to pass to the Windows launch boundary."""

    root: Path
    executable: Path
    tessdata_root: Path
    resources: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class _ManifestFile:
    path: PurePosixPath
    size_bytes: int
    sha256: str


def packaged_tesseract_runtime_manifest() -> Path:
    """Return the code-owned manifest location; callers cannot replace it."""

    return Path(__file__).absolute().with_name(_TESSERACT_RUNTIME_MANIFEST_NAME)


def admit_packaged_tesseract_runtime(
    executable: Path,
    tessdata_root: Path,
) -> TesseractRuntimeAdmission:
    """Admit only the closure pinned by the packaged, reviewed manifest."""

    manifest_path = packaged_tesseract_runtime_manifest()
    manifest_bytes = _read_packaged_manifest(manifest_path)
    return admit_tesseract_runtime(
        executable,
        tessdata_root,
        manifest_bytes=manifest_bytes,
        expected_manifest_sha256=TESSERACT_RUNTIME_MANIFEST_SHA256,
    )


def admit_tesseract_runtime(
    executable: Path,
    tessdata_root: Path,
    *,
    manifest_bytes: bytes,
    expected_manifest_sha256: str,
) -> TesseractRuntimeAdmission:
    """Authenticate one closure; the injectable bytes are a pure test seam."""

    if (
        not isinstance(executable, Path)
        or not isinstance(tessdata_root, Path)
        or not executable.is_absolute()
        or not tessdata_root.is_absolute()
        or type(manifest_bytes) is not bytes
        or not manifest_bytes
        or len(manifest_bytes) > _MAX_MANIFEST_BYTES
        or type(expected_manifest_sha256) is not str
        or len(expected_manifest_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_manifest_sha256)
    ):
        _reject("configuration")
    if hashlib.sha256(manifest_bytes).hexdigest() != expected_manifest_sha256:
        _reject("manifest_hash")

    files = _parse_manifest(manifest_bytes)
    root = executable.parent.parent
    expected_executable = root.joinpath(*TESSERACT_RUNTIME_ENTRYPOINT.parts)
    expected_tessdata = root.joinpath(*TESSERACT_RUNTIME_TESSDATA_DIRECTORY.parts)
    if _normalized(executable) != _normalized(expected_executable) or _normalized(
        tessdata_root
    ) != _normalized(expected_tessdata):
        _reject("layout")

    expected_files = {entry.path.as_posix(): entry for entry in files}
    expected_directories = _expected_directories(files)
    observed_files, observed_directories = _inventory_runtime(root)
    if observed_files - expected_files.keys() or observed_directories - expected_directories:
        _reject("unlisted")
    if expected_files.keys() - observed_files or expected_directories - observed_directories:
        _reject("missing")

    for entry in files:
        _verify_file(root.joinpath(*entry.path.parts), entry)

    directories = tuple(
        root.joinpath(*PurePosixPath(relative).parts)
        for relative in sorted(expected_directories, key=lambda item: (item.count("/"), item))
    )
    resources = (
        root,
        *directories,
        *(root.joinpath(*entry.path.parts) for entry in files),
    )
    return TesseractRuntimeAdmission(
        root=root,
        executable=expected_executable,
        tessdata_root=expected_tessdata,
        resources=resources,
    )


def _read_packaged_manifest(path: Path) -> bytes:
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or _is_reparse(metadata)
            or metadata.st_size <= 0
            or metadata.st_size > _MAX_MANIFEST_BYTES
            or metadata.st_nlink != 1
        ):
            _reject("manifest_resource")
        content = path.read_bytes()
        after = path.lstat()
    except OSError, ValueError:
        _reject("manifest_resource")
    if _file_identity(metadata) != _file_identity(after):
        _reject("manifest_resource")
    return content


def _parse_manifest(content: bytes) -> tuple[_ManifestFile, ...]:
    try:
        decoded = content.decode("utf-8")
        document = json.loads(
            decoded,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except UnicodeDecodeError, json.JSONDecodeError, TesseractRuntimeAdmissionError:
        _reject("manifest_schema")
    root = _mapping(document)
    layout = _mapping(root.get("layout"))
    closure = _mapping(root.get("closure_evidence"))
    raw_files = root.get("files")
    if (
        root.get("schema_version") != 1
        or root.get("manifest_id") != "paperworkshell-tesseract-runtime-windows-x86_64"
        or root.get("task_id") != "PWS-0204"
        or layout.get("entrypoint") != TESSERACT_RUNTIME_ENTRYPOINT.as_posix()
        or layout.get("tessdata_directory") != TESSERACT_RUNTIME_TESSDATA_DIRECTORY.as_posix()
        or layout.get("allow_unlisted_files") is not False
        or layout.get("allow_reparse_points") is not False
        or closure.get("selected_file_count") != TESSERACT_RUNTIME_FILE_COUNT
        or type(raw_files) is not list
        or len(raw_files) != TESSERACT_RUNTIME_FILE_COUNT
    ):
        _reject("manifest_schema")

    files: list[_ManifestFile] = []
    seen: set[str] = set()
    for raw_entry in raw_files:
        entry = _mapping(raw_entry)
        raw_path = entry.get("path")
        size_bytes = entry.get("size_bytes")
        digest = entry.get("sha256")
        if (
            type(raw_path) is not str
            or type(size_bytes) is not int
            or size_bytes < 0
            or type(digest) is not str
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            _reject("manifest_schema")
        parsed_path = PurePosixPath(raw_path)
        if (
            not raw_path
            or "\\" in raw_path
            or "\x00" in raw_path
            or ":" in raw_path
            or parsed_path.is_absolute()
            or parsed_path.as_posix() != raw_path
            or any(part in {"", ".", ".."} for part in parsed_path.parts)
            or len(parsed_path.parts) > _MAX_RUNTIME_DEPTH
        ):
            _reject("manifest_schema")
        key = raw_path.casefold()
        if key in seen:
            _reject("manifest_schema")
        seen.add(key)
        files.append(_ManifestFile(parsed_path, size_bytes, digest))

    files.sort(key=lambda item: item.path.as_posix())
    if (
        TESSERACT_RUNTIME_ENTRYPOINT.as_posix() not in seen
        or not any(
            entry.path == TESSERACT_RUNTIME_TESSDATA_DIRECTORY / "eng.traineddata"
            for entry in files
        )
        or sum(entry.size_bytes for entry in files) != closure.get("selected_file_bytes")
    ):
        _reject("manifest_schema")
    return tuple(files)


def _expected_directories(files: tuple[_ManifestFile, ...]) -> set[str]:
    directories: set[str] = set()
    for entry in files:
        parent = entry.path.parent
        while parent != PurePosixPath("."):
            directories.add(parent.as_posix())
            parent = parent.parent
    return directories


def _inventory_runtime(root: Path) -> tuple[set[str], set[str]]:
    observed_files: set[str] = set()
    observed_directories: set[str] = set()
    pending: list[tuple[Path, PurePosixPath, int]] = [(root, PurePosixPath("."), 0)]
    entry_count = 0
    try:
        root_metadata = root.lstat()
        if not stat.S_ISDIR(root_metadata.st_mode) or _is_reparse(root_metadata):
            _reject("reparse")
        while pending:
            directory, relative_directory, depth = pending.pop()
            if depth > _MAX_RUNTIME_DEPTH:
                _reject("inventory")
            with os.scandir(directory) as children:
                entries = sorted(children, key=lambda child: child.name.casefold())
            for child in entries:
                entry_count += 1
                if entry_count > _MAX_RUNTIME_ENTRIES:
                    _reject("inventory")
                metadata = child.stat(follow_symlinks=False)
                if child.is_symlink() or _is_reparse(metadata):
                    _reject("reparse")
                relative = (
                    PurePosixPath(child.name)
                    if relative_directory == PurePosixPath(".")
                    else relative_directory / child.name
                )
                rendered = relative.as_posix()
                if stat.S_ISDIR(metadata.st_mode):
                    observed_directories.add(rendered)
                    pending.append((Path(child.path), relative, depth + 1))
                elif stat.S_ISREG(metadata.st_mode):
                    observed_files.add(rendered)
                else:
                    _reject("type")
    except TesseractRuntimeAdmissionError:
        raise
    except OSError, ValueError:
        _reject("inventory")
    return observed_files, observed_directories


def _verify_file(path: Path, expected: _ManifestFile) -> None:
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or _is_reparse(before) or before.st_nlink != 1:
            _reject("type")
        if before.st_size != expected.size_bytes:
            _reject("size")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if _file_identity(before) != _file_identity(opened):
                _reject("changed")
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        after = path.lstat()
    except TesseractRuntimeAdmissionError:
        raise
    except OSError, ValueError:
        _reject("missing")
    if _file_identity(before) != _file_identity(after):
        _reject("changed")
    if digest.hexdigest() != expected.sha256:
        _reject("hash")


def _file_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        int(getattr(metadata, "st_file_attributes", 0)),
    )


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(int(getattr(metadata, "st_file_attributes", 0)) & _FILE_ATTRIBUTE_REPARSE_POINT)


def _normalized(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _reject("manifest_schema")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> Never:
    _reject("manifest_schema")


def _mapping(value: object) -> dict[str, Any]:
    if type(value) is not dict:
        _reject("manifest_schema")
    return value


def _reject(context: str) -> Never:
    error = TesseractRuntimeAdmissionError(context=context)
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None

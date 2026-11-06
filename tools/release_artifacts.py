"""Bounded, hash-first helpers for maintainer-owned release inputs.

These utilities never discover user files or run downloaded code. Network retrieval
is an explicit build action; ordinary validation and document processing stay offline.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import time
import urllib.request
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final
from urllib.parse import urlsplit

MAX_ARTIFACT_BYTES: Final = 512 * 1024 * 1024
MAX_ARTIFACT_SECONDS: Final = 600
MAX_ZIP_FILES: Final = 10_000
MAX_STAGE_BYTES: Final = 2 * 1024 * 1024 * 1024
CHUNK_BYTES: Final = 1024 * 1024
_RESERVED: Final = frozenset(
    {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10))}
)


class ReleaseArtifactError(ValueError):
    """A closed error code; no supplied path, URL or upstream text."""


@dataclass(frozen=True)
class Artifact:
    name: str
    url: str
    size_bytes: int
    sha256: str


def safe_relative(value: str) -> str:
    """Require one portable, case-unambiguous Windows relative path."""
    if (
        not value
        or len(value) > 512
        or any(ord(c) < 32 or ord(c) > 126 or c in '\\:<>"|?*' for c in value)
        or value.startswith("/")
        or PurePosixPath(value).as_posix() != value
    ):
        raise ReleaseArtifactError("RELEASE_PATH")
    for part in value.split("/"):
        if (
            part in {"", ".", ".."}
            or part.endswith((".", " "))
            or part.split(".")[0].casefold() in _RESERVED
        ):
            raise ReleaseArtifactError("RELEASE_PATH")
    return value


def plain_path(path: Path) -> None:
    """Reject filesystem redirection in every existing path component."""
    for item in (*reversed(path.absolute().parents), path.absolute()):
        if not item.exists() and not item.is_symlink():
            continue
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ReleaseArtifactError("RELEASE_REPARSE_POINT")


def file_hashes(path: Path, maximum: int = MAX_ARTIFACT_BYTES) -> tuple[int, str, str]:
    plain_path(path)
    sha256, sha1 = hashlib.sha256(), hashlib.sha1(usedforsecurity=False)
    size = 0
    with path.open("rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ReleaseArtifactError("RELEASE_FILE")
        while chunk := source.read(CHUNK_BYTES):
            size += len(chunk)
            if size > maximum:
                raise ReleaseArtifactError("RELEASE_LIMIT")
            sha256.update(chunk)
            sha1.update(chunk)
    return size, sha256.hexdigest(), sha1.hexdigest()


def verify_artifact(path: Path, expected: Artifact) -> None:
    if (
        safe_relative(expected.name) != Path(expected.name).name
        or not 0 < expected.size_bytes <= MAX_ARTIFACT_BYTES
        or re.fullmatch(r"[0-9a-f]{64}", expected.sha256) is None
    ):
        raise ReleaseArtifactError("RELEASE_ARTIFACT_IDENTITY")
    size, digest, _ = file_hashes(path, expected.size_bytes)
    if size != expected.size_bytes or digest != expected.sha256:
        raise ReleaseArtifactError("RELEASE_ARTIFACT_HASH")


def acquire(expected: Artifact, directory: Path) -> Path:
    """Retrieve only an explicit HTTPS artifact and never replace an existing input."""
    location = urlsplit(expected.url)
    if (
        location.scheme != "https"
        or not location.hostname
        or location.username
        or location.password
        or location.fragment
    ):
        raise ReleaseArtifactError("RELEASE_SOURCE")
    safe_relative(expected.name)
    if Path(expected.name).name != expected.name:
        raise ReleaseArtifactError("RELEASE_PATH")
    plain_path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / expected.name
    if target.exists():
        verify_artifact(target, expected)
        return target
    # Identity must be reviewed before fetching or trusting the input.
    if not 0 < expected.size_bytes <= MAX_ARTIFACT_BYTES or not re.fullmatch(
        r"[0-9a-f]{64}", expected.sha256
    ):
        raise ReleaseArtifactError("RELEASE_ARTIFACT_IDENTITY")
    started = time.monotonic()
    created = False
    try:
        with target.open("xb") as output:
            created = True
            with urllib.request.urlopen(expected.url, timeout=30) as response:
                if urlsplit(response.url).scheme != "https":
                    raise ReleaseArtifactError("RELEASE_SOURCE")
                size = 0
                while chunk := response.read(CHUNK_BYTES):
                    size += len(chunk)
                    if (
                        size > expected.size_bytes
                        or time.monotonic() - started > MAX_ARTIFACT_SECONDS
                    ):
                        raise ReleaseArtifactError("RELEASE_LIMIT")
                    output.write(chunk)
        verify_artifact(target, expected)
    except BaseException:
        if created:
            target.unlink(missing_ok=True)
        raise
    return target


def extract_selected_zip(
    archive_path: Path,
    expected: Artifact,
    destination: Path,
    selected: Mapping[str, str],
) -> list[str]:
    """Copy an explicit regular-file subset after whole-input authentication.

    The caller owns a new stage. Existing destination files are never overwritten.
    No member is extracted through zipfile's path-handling convenience APIs.
    """
    verify_artifact(archive_path, expected)
    plain_path(destination)
    if not selected or len(selected) > MAX_ZIP_FILES:
        raise ReleaseArtifactError("RELEASE_SELECTION")
    outputs = [safe_relative(value) for value in selected.values()]
    if len(set(value.casefold() for value in outputs)) != len(outputs):
        raise ReleaseArtifactError("RELEASE_COLLISION")
    for key in selected:
        safe_relative(key)
    written: list[Path] = []
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            if len(members) > MAX_ZIP_FILES:
                raise ReleaseArtifactError("RELEASE_LIMIT")
            names: set[str] = set()
            total = 0
            for member in members:
                safe_relative(member.orig_filename.rstrip("/"))
                normalized = safe_relative(member.filename.rstrip("/"))
                folded = normalized.casefold()
                if folded in names:
                    raise ReleaseArtifactError("RELEASE_COLLISION")
                names.add(folded)
                if stat.S_IFMT(member.external_attr >> 16) not in {0, stat.S_IFREG, stat.S_IFDIR}:
                    raise ReleaseArtifactError("RELEASE_ARCHIVE_MEMBER")
                total += member.file_size
                if total > MAX_STAGE_BYTES or member.flag_bits & 1:
                    raise ReleaseArtifactError("RELEASE_LIMIT")
            by_name = {item.filename: item for item in members}
            for name, output_name in sorted(selected.items()):
                selected_member = by_name.get(name)
                if (
                    selected_member is None
                    or selected_member.is_dir()
                    or stat.S_IFMT(selected_member.external_attr >> 16) not in {0, stat.S_IFREG}
                    or selected_member.file_size > MAX_ARTIFACT_BYTES
                ):
                    raise ReleaseArtifactError("RELEASE_SELECTION")
                member = selected_member
                output_path = destination / output_name
                plain_path(output_path)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with output_path.open("xb") as output:
                    written.append(output_path)
                    with archive.open(member) as source:
                        copied = 0
                        while chunk := source.read(CHUNK_BYTES):
                            copied += len(chunk)
                            if copied > member.file_size:
                                raise ReleaseArtifactError("RELEASE_LIMIT")
                            output.write(chunk)
                if copied != member.file_size:
                    raise ReleaseArtifactError("RELEASE_ARCHIVE_MEMBER")
    except BaseException:
        for path in reversed(written):
            path.unlink(missing_ok=True)
        raise
    return sorted(outputs)

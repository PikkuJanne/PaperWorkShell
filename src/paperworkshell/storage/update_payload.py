"""Validate and stage authenticated PaperWorkShell Windows runtime bundles."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import struct
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO, Final, cast

from paperworkshell.domain.product_identity import INSTALLED_APPLICATION_ID
from paperworkshell.domain.update_verification import MAX_PACKAGE_BYTES, UpdateMetadata

MANIFEST_NAME: Final = "update-payload-v1.json"
INSTALLED_MARKER_NAME: Final = "paperworkshell-installed-v1.json"
MAX_INSTALLED_MARKER_BYTES: Final = 4096
MAX_MANIFEST_BYTES: Final = 5 * 1024 * 1024
MAX_PAYLOAD_FILES: Final = 10_000
MAX_PAYLOAD_BYTES: Final = 2 * 1024 * 1024 * 1024
MAX_MEMBER_BYTES: Final = 512 * 1024 * 1024
_MAX_CENTRAL_DIRECTORY_BYTES: Final = 8 * 1024 * 1024
_CHUNK_BYTES: Final = 1024 * 1024
_ZIP_END: Final = struct.Struct("<4s4H2LH")
_UNSAFE_ATTRIBUTES: Final = 0x400 | 0x1000 | 0x40000 | 0x400000
_RESERVED: Final = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        "conin$",
        "conout$",
        "clock$",
        *(f"com{i}" for i in range(10)),
        *(f"lpt{i}" for i in range(10)),
    }
)
_GROUPS: Final = frozenset({"runtime", "notices", "sources", "evidence"})
_MARKER_PATH: Final = "runtime/" + INSTALLED_MARKER_NAME
_RECIPIENT_TERMS_PATH: Final = "notices/Microsoft-runtime-recipient-terms.txt"
_REQUIRED_FILES: Final = frozenset(
    {
        _MARKER_PATH,
        "runtime/PaperWorkShellDesktop.exe",
        "runtime/PaperWorkShell.exe",
        "runtime/python.exe",
        "runtime/python314._pth",
    }
)
_CHANNEL: Final = re.compile(r"[a-z][a-z0-9-]{0,31}")
_VERSION: Final = re.compile(r"(?:0|[1-9][0-9]{0,3})(?:\.(?:0|[1-9][0-9]{0,3})){2}")
_CANDIDATE: Final = re.compile(r"[a-z0-9][a-z0-9.-]{0,95}")
_COMPONENT_ID: Final = re.compile(r"[a-z0-9][a-z0-9.+-]{0,127}")
_SHA256: Final = re.compile(r"[0-9a-f]{64}")


class UpdatePayloadErrorCode(StrEnum):
    INVALID = "UPDATE_PAYLOAD_INVALID"
    IDENTITY_MISMATCH = "UPDATE_PAYLOAD_IDENTITY_MISMATCH"
    SCHEMA_MISMATCH = "UPDATE_PAYLOAD_SCHEMA_MISMATCH"
    STAGE_UNAVAILABLE = "UPDATE_PAYLOAD_STAGE_UNAVAILABLE"
    CANCELLED = "UPDATE_PAYLOAD_CANCELLED"


_MESSAGES: Final = {
    UpdatePayloadErrorCode.INVALID: "The PaperWorkShell update payload is invalid.",
    UpdatePayloadErrorCode.IDENTITY_MISMATCH: "The payload does not match the signed release.",
    UpdatePayloadErrorCode.SCHEMA_MISMATCH: "The update requires a different case format.",
    UpdatePayloadErrorCode.STAGE_UNAVAILABLE: "The PaperWorkShell update could not be staged.",
    UpdatePayloadErrorCode.CANCELLED: "The PaperWorkShell update was cancelled.",
}


class UpdatePayloadError(ValueError):
    """A payload failure with no user paths or untrusted archive contents."""

    def __init__(self, code: UpdatePayloadErrorCode) -> None:
        self.code = code
        super().__init__(_MESSAGES[code])


@dataclass(frozen=True, slots=True)
class UpdatePayloadFile:
    path: str
    size_bytes: int
    sha256: str
    component_id: str


@dataclass(frozen=True, slots=True)
class UpdatePayload:
    channel: str
    version: str
    candidate_id: str
    case_schema_version: int
    inventory_sha256: str
    manifest_sha256: str
    components: tuple[tuple[str, str], ...]
    files: tuple[UpdatePayloadFile, ...]

    @property
    def recipient_terms_sha256(self) -> str | None:
        return next(
            (member.sha256 for member in self.files if member.path == _RECIPIENT_TERMS_PATH),
            None,
        )


def _invalid() -> UpdatePayloadError:
    return UpdatePayloadError(UpdatePayloadErrorCode.INVALID)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _invalid()
        result[key] = value
    return result


def _json(raw: bytes) -> object:
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except ValueError, RecursionError:
        raise _invalid() from None


def _object(value: object, keys: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or value.keys() != keys:
        raise _invalid()
    return cast("dict[str, object]", value)


def _text(value: object, pattern: re.Pattern[str]) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise _invalid()
    return value


def _integer(value: object, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise _invalid()
    return value


def _relative_path(value: object) -> str:
    if type(value) is not str or not 0 < len(value) <= 512:
        raise _invalid()
    if any(ord(char) < 32 or ord(char) > 126 or char in '\\:<>"|?*' for char in value):
        raise _invalid()
    parts = value.split("/")
    if not 2 <= len(parts) <= 64 or parts[0] not in _GROUPS:
        raise _invalid()
    for part in parts:
        if (
            part in {"", ".", ".."}
            or part.endswith((".", " "))
            or part.split(".")[0].rstrip().casefold() in _RESERVED
        ):
            raise _invalid()
    return value


def _parse_components(value: object) -> dict[str, str]:
    if not isinstance(value, list) or not 0 < len(value) <= 1024:
        raise _invalid()
    components: dict[str, str] = {}
    for entry in value:
        item = _object(entry, {"component_id", "version"})
        component_id = _text(item["component_id"], _COMPONENT_ID)
        version = item["version"]
        if (
            component_id in components
            or type(version) is not str
            or not 0 < len(version) <= 2048
            or not version.isprintable()
        ):
            raise _invalid()
        components[component_id] = version
    return components


def _parse_files(value: object, component_ids: dict[str, str]) -> tuple[UpdatePayloadFile, ...]:
    if not isinstance(value, list) or not 0 < len(value) <= MAX_PAYLOAD_FILES:
        raise _invalid()
    files: dict[str, UpdatePayloadFile] = {}
    directory_names: dict[str, str] = {}
    total = 0
    for entry in value:
        item = _object(entry, {"path", "size_bytes", "sha256", "component_id"})
        path = _relative_path(item["path"])
        size = _integer(item["size_bytes"], 0, MAX_MEMBER_BYTES)
        digest = _text(item["sha256"], _SHA256)
        owner = _text(item["component_id"], _COMPONENT_ID)
        folded = path.casefold()
        total += size
        if (
            owner not in component_ids
            or folded in files
            or folded in directory_names
            or total > MAX_PAYLOAD_BYTES
        ):
            raise _invalid()
        files[folded] = UpdatePayloadFile(path, size, digest, owner)
        directories = path.split("/")[:-1]
        for depth in range(1, len(directories) + 1):
            directory = "/".join(directories[:depth])
            folded_directory = directory.casefold()
            prior = directory_names.setdefault(folded_directory, directory)
            if folded_directory in files or prior != directory:
                raise _invalid()
    if not _REQUIRED_FILES.issubset(member.path for member in files.values()):
        raise _invalid()
    if not 0 < files[_MARKER_PATH].size_bytes <= MAX_INSTALLED_MARKER_BYTES:
        raise _invalid()
    return tuple(files.values())


def parse_payload_manifest(raw: bytes) -> UpdatePayload:
    """Validate a bounded manifest, independently of where its bytes came from."""
    if not 0 < len(raw) <= MAX_MANIFEST_BYTES:
        raise _invalid()
    manifest = _object(
        _json(raw),
        {
            "schema_version",
            "application_id",
            "channel",
            "version",
            "candidate_id",
            "case_schema_version",
            "inventory_sha256",
            "components",
            "files",
        },
    )
    if (
        type(manifest["schema_version"]) is not int
        or manifest["schema_version"] != 1
        or manifest["application_id"] != INSTALLED_APPLICATION_ID
    ):
        raise _invalid()
    components = _parse_components(manifest["components"])
    return UpdatePayload(
        channel=_text(manifest["channel"], _CHANNEL),
        version=_text(manifest["version"], _VERSION),
        candidate_id=_text(manifest["candidate_id"], _CANDIDATE),
        case_schema_version=_integer(manifest["case_schema_version"], 1, 65_535),
        inventory_sha256=_text(manifest["inventory_sha256"], _SHA256),
        manifest_sha256=hashlib.sha256(raw).hexdigest(),
        components=tuple(sorted(components.items())),
        files=_parse_files(manifest["files"], components),
    )


def verify_payload_marker(raw: bytes, manifest: UpdatePayload) -> None:
    """Bind the installed runtime marker to the authenticated payload identity."""
    if not 0 < len(raw) <= MAX_INSTALLED_MARKER_BYTES:
        raise _invalid()
    marker = _object(
        _json(raw), {"schema_version", "application_id", "channel", "version", "candidate_id"}
    )
    if (
        type(marker["schema_version"]) is not int
        or marker["schema_version"] != 1
        or marker["application_id"] != INSTALLED_APPLICATION_ID
        or marker["channel"] != manifest.channel
        or marker["version"] != manifest.version
        or marker["candidate_id"] != manifest.candidate_id
    ):
        raise UpdatePayloadError(UpdatePayloadErrorCode.IDENTITY_MISMATCH)


def _check_stage(stage: Path, *, empty: bool) -> None:
    if (
        not stage.is_absolute()
        or stage.drive.startswith("\\\\")
        or ".." in stage.parts
        or len(stage.parts) > 64
    ):
        raise UpdatePayloadError(UpdatePayloadErrorCode.STAGE_UNAVAILABLE)
    try:
        for part in (*reversed(stage.parents), stage):
            info = part.lstat()
            if not stat.S_ISDIR(info.st_mode) or (
                getattr(info, "st_file_attributes", 0) & _UNSAFE_ATTRIBUTES
            ):
                raise UpdatePayloadError(UpdatePayloadErrorCode.STAGE_UNAVAILABLE)
        if empty and next(stage.iterdir(), None) is not None:
            raise UpdatePayloadError(UpdatePayloadErrorCode.STAGE_UNAVAILABLE)
    except OSError:
        raise UpdatePayloadError(UpdatePayloadErrorCode.STAGE_UNAVAILABLE) from None


def _archive_member_count(stream: BinaryIO, package_size: int) -> int:
    """Bound ZIP metadata before the standard library allocates its central directory."""
    if not _ZIP_END.size < package_size <= MAX_PACKAGE_BYTES:
        raise _invalid()
    if stream.seek(0, 2) != package_size:
        raise _invalid()
    stream.seek(package_size - _ZIP_END.size)
    magic, disk, central_disk, local_count, count, size, offset, comment = _ZIP_END.unpack(
        stream.read(_ZIP_END.size)
    )
    if (
        magic != b"PK\x05\x06"
        or disk != 0
        or central_disk != 0
        or local_count != count
        or not 1 < count <= MAX_PAYLOAD_FILES + 1
        or not 0 < size <= _MAX_CENTRAL_DIRECTORY_BYTES
        or offset + size != package_size - _ZIP_END.size
        or comment != 0
    ):
        raise _invalid()
    stream.seek(0)
    if stream.read(4) != b"PK\x03\x04":
        raise _invalid()
    return cast(int, count)


def _members(archive: zipfile.ZipFile, count: int) -> dict[str, zipfile.ZipInfo]:
    listed = archive.infolist()
    if len(listed) != count:
        raise _invalid()
    members: dict[str, zipfile.ZipInfo] = {}
    names: set[str] = set()
    for member in listed:
        name = member.orig_filename
        folded = name.casefold()
        if (
            name != member.filename
            or folded in names
            or member.is_dir()
            or stat.S_IFMT(member.external_attr >> 16) not in {0, stat.S_IFREG}
            or member.external_attr & 0x10
            or member.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
            or member.flag_bits & ~0x800
            or member.extra
            or member.comment
        ):
            raise _invalid()
        if name != MANIFEST_NAME:
            _relative_path(name)
        names.add(folded)
        members[name] = member
    return members


def _copy_member(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    expected: UpdatePayloadFile,
    stage: Path,
    cancelled: Callable[[], bool],
    checkpoint: Callable[[], None],
) -> None:
    checkpoint()
    target = stage / expected.path
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    remaining = expected.size_bytes
    with archive.open(member) as source, target.open("xb") as output:
        while True:
            checkpoint()
            if cancelled():
                raise UpdatePayloadError(UpdatePayloadErrorCode.CANCELLED)
            chunk = source.read(min(_CHUNK_BYTES, remaining + 1))
            if not chunk:
                break
            if len(chunk) > remaining:
                raise _invalid()
            remaining -= len(chunk)
            digest.update(chunk)
            output.write(chunk)
    if remaining or digest.hexdigest() != expected.sha256:
        raise _invalid()


def extract_update_payload(
    stream: BinaryIO,
    stage: Path,
    metadata: UpdateMetadata,
    *,
    installed_case_schema_version: int,
    cancelled: Callable[[], bool] = lambda: False,
    checkpoint: Callable[[], None] | None = None,
) -> UpdatePayload:
    """Extract from the same, unchanged seekable handle authenticated by the caller.

    ZIP root paths are relative to an owned empty stage. Invalid or cancelled
    extraction never activates the result. The caller owns stage cleanup.
    """
    progress = checkpoint if checkpoint is not None else lambda: None
    progress()
    _check_stage(stage, empty=True)
    try:
        try:
            count = _archive_member_count(stream, metadata.package_size)
        except ValueError as error:
            if isinstance(error, UpdatePayloadError):
                raise
            raise _invalid() from None
        with zipfile.ZipFile(stream) as archive:
            members = _members(archive, count)
            info = members.pop(MANIFEST_NAME, None)
            if info is None or not 0 < info.file_size <= MAX_MANIFEST_BYTES:
                raise _invalid()
            raw = archive.read(info)
            manifest = parse_payload_manifest(raw)
            if (manifest.channel, manifest.version, manifest.candidate_id) != (
                metadata.channel,
                metadata.version,
                metadata.candidate_id,
            ):
                raise UpdatePayloadError(UpdatePayloadErrorCode.IDENTITY_MISMATCH)
            if manifest.case_schema_version != installed_case_schema_version:
                raise UpdatePayloadError(UpdatePayloadErrorCode.SCHEMA_MISMATCH)
            expected = {member.path: member for member in manifest.files}
            if members.keys() != expected.keys() or any(
                member.file_size != expected[path].size_bytes for path, member in members.items()
            ):
                raise _invalid()
            verify_payload_marker(archive.read(members[_MARKER_PATH]), manifest)
            for path, member in members.items():
                _copy_member(archive, member, expected[path], stage, cancelled, progress)
        progress()
        if cancelled():
            raise UpdatePayloadError(UpdatePayloadErrorCode.CANCELLED)
        with (stage / MANIFEST_NAME).open("xb") as output:
            output.write(raw)
    except UpdatePayloadError:
        raise
    except zipfile.BadZipFile, EOFError, struct.error, NotImplementedError, zlib.error:
        raise _invalid() from None
    except OSError:
        raise UpdatePayloadError(UpdatePayloadErrorCode.STAGE_UNAVAILABLE) from None
    return manifest


def _plain_file(info: os.stat_result) -> bool:
    return (
        stat.S_ISREG(info.st_mode)
        and info.st_nlink == 1
        and not getattr(info, "st_file_attributes", 0) & _UNSAFE_ATTRIBUTES
    )


def _file_identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        stat.S_IFMT(info.st_mode),
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        getattr(info, "st_file_attributes", 0),
    )


def _verify_staged_file(
    path: Path, member: UpdatePayloadFile, checkpoint: Callable[[], None]
) -> None:
    checkpoint()
    before = path.lstat()
    if not _plain_file(before) or before.st_size != member.size_bytes:
        raise _invalid()
    digest = hashlib.sha256()
    remaining = member.size_bytes
    with path.open("rb") as source:
        if _file_identity(os.fstat(source.fileno())) != _file_identity(before):
            raise _invalid()
        while chunk := source.read(min(_CHUNK_BYTES, remaining + 1)):
            checkpoint()
            if len(chunk) > remaining:
                raise _invalid()
            remaining -= len(chunk)
            digest.update(chunk)
        if _file_identity(os.fstat(source.fileno())) != _file_identity(before):
            raise _invalid()
    if (
        remaining
        or digest.hexdigest() != member.sha256
        or _file_identity(path.lstat()) != _file_identity(before)
    ):
        raise _invalid()


def _verify_staged_paths(stage: Path, files: set[str], checkpoint: Callable[[], None]) -> None:
    directories = {
        "/".join(parts[:depth])
        for path in files
        for parts in (path.split("/"),)
        for depth in range(1, len(parts))
    }
    remaining = files.copy()
    pending = [stage]
    while pending:
        checkpoint()
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                path = Path(entry.path)
                name = path.relative_to(stage).as_posix()
                info = path.lstat()
                if stat.S_ISDIR(info.st_mode):
                    if name not in directories or (
                        getattr(info, "st_file_attributes", 0) & _UNSAFE_ATTRIBUTES
                    ):
                        raise _invalid()
                    pending.append(path)
                elif name in remaining and _plain_file(info):
                    remaining.remove(name)
                else:
                    raise _invalid()
    if remaining:
        raise _invalid()


def validate_installed_update_payload(
    stage: Path,
    *,
    expected_manifest_sha256: str,
    checkpoint: Callable[[], None] | None = None,
) -> UpdatePayload:
    """Revalidate an exact retained slot through its trusted manifest digest."""
    progress = checkpoint if checkpoint is not None else lambda: None
    progress()
    _check_stage(stage, empty=False)
    try:
        path = stage / MANIFEST_NAME
        info = path.lstat()
        if not _plain_file(info) or not 0 < info.st_size <= MAX_MANIFEST_BYTES:
            raise _invalid()
        with path.open("rb") as source:
            if _file_identity(os.fstat(source.fileno())) != _file_identity(info):
                raise _invalid()
            raw = source.read(MAX_MANIFEST_BYTES + 1)
        if hashlib.sha256(raw).hexdigest() != expected_manifest_sha256:
            raise _invalid()
        manifest = parse_payload_manifest(raw)
        _verify_staged_paths(
            stage, {member.path for member in manifest.files} | {MANIFEST_NAME}, progress
        )
        for member in manifest.files:
            _verify_staged_file(stage / member.path, member, progress)
        with (stage / _MARKER_PATH).open("rb") as marker:
            verify_payload_marker(marker.read(MAX_INSTALLED_MARKER_BYTES + 1), manifest)
        if _file_identity(path.lstat()) != _file_identity(info):
            raise _invalid()
    except OSError:
        raise UpdatePayloadError(UpdatePayloadErrorCode.STAGE_UNAVAILABLE) from None
    return manifest

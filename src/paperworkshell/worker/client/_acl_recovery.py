"""Strict durable journal primitives for Windows worker ACL recovery.

The journal contains trusted runtime-resource paths because recovery cannot undo an
interrupted SID grant without them.  Those values never cross the worker protocol or
routine diagnostic boundary, and none of the models in this module expose them from
``repr``.  The Windows adapter authenticates the containing directory and each
recorded object before using a decoded entry as mutation authority.
"""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from secrets import token_hex
from typing import Final

ACL_RECOVERY_LEGACY_VERSION: Final = 1
ACL_RECOVERY_JOURNAL_VERSION: Final = 2
MAX_ACL_RECOVERY_ENTRIES: Final = 128
MAX_ACL_RECOVERY_GRANT_ROOTS: Final = 32
MAX_ACL_RECOVERY_OBJECTS: Final = 20_000
MAX_ACL_RECOVERY_BYTES: Final = 32 * 1024 * 1024
MAX_ACL_RECOVERY_PATH_CODEPOINTS: Final = 32_767
MAX_ACL_RECOVERY_JSON_NODES: Final = (
    12 + MAX_ACL_RECOVERY_GRANT_ROOTS + (9 * MAX_ACL_RECOVERY_OBJECTS)
)
MAX_ACL_RECOVERY_JSON_DEPTH: Final = 8

_PROFILE_ID = re.compile(r"[0-9a-f]{24}\Z")
_PROFILE_NAME = re.compile(r"PaperWorkShell\.Worker\.[0-9a-f]{24}\Z")
_APP_CONTAINER_SID = re.compile(r"S-1-15-2(?:-[0-9]{1,10}){7}\Z")
_WINDOWS_SID = re.compile(r"S-1-[0-9]{1,20}(?:-[0-9]{1,10}){1,15}\Z")
_FILE_ID = re.compile(r"[0-9a-f]{32}\Z")
_LEDGER_NAME = re.compile(r"worker-acl-([0-9a-f]{24})\.json\Z")
_STAGE_NAME = re.compile(r"worker-acl-([0-9a-f]{24})\.([0-9a-f]{24})\.tmp\Z")
_REPARSE_ATTRIBUTE: Final = 0x0000_0400
_V1_FIELDS: Final = frozenset(
    {
        "journal_version",
        "profile_id",
        "profile_name",
        "sid",
        "owner_pid",
        "owner_created_100ns",
        "phase",
        "grant_roots",
        "objects",
    }
)
_V2_FIELDS: Final = _V1_FIELDS | {"peer_sid"}
_V1_OBJECT_FIELDS: Final = frozenset({"path", "device", "inode", "is_directory", "auto_inherited"})
_V2_OBJECT_FIELDS: Final = _V1_OBJECT_FIELDS | {
    "file_id",
    "guard_baseline",
    "volume_serial",
}


class AclRecoveryPhase(StrEnum):
    """Durable cut points around a temporary AppContainer SID grant."""

    PREPARED = "prepared"
    ACTIVE = "active"
    REVOKING = "revoking"


@dataclass(frozen=True, slots=True, repr=False)
class AclRecoveryObject:
    """One exact filesystem object and its pre-grant inheritance state."""

    path: Path
    device: int
    inode: int
    is_directory: bool
    auto_inherited: bool
    volume_serial: int | None = None
    file_id: str | None = None
    guard_baseline: int | None = None

    def __post_init__(self) -> None:
        rendered = os.fspath(self.path)
        if (
            not isinstance(self.path, Path)
            or not self.path.is_absolute()
            or not rendered
            or "\x00" in rendered
            or len(rendered) > MAX_ACL_RECOVERY_PATH_CODEPOINTS
            or type(self.device) is not int
            or self.device < 0
            or type(self.inode) is not int
            or self.inode < 1
            or type(self.is_directory) is not bool
            or type(self.auto_inherited) is not bool
            or (
                self.volume_serial is not None
                and (type(self.volume_serial) is not int or self.volume_serial < 0)
            )
            or (
                self.file_id is not None
                and (type(self.file_id) is not str or _FILE_ID.fullmatch(self.file_id) is None)
            )
            or (
                self.guard_baseline is not None
                and (type(self.guard_baseline) is not int or not 0 <= self.guard_baseline <= 0xFFFF)
            )
        ):
            raise ValueError("invalid ACL recovery object")

    def __repr__(self) -> str:
        return "AclRecoveryObject(<authenticated resource>)"


@dataclass(frozen=True, slots=True, repr=False)
class AclRecoveryEntry:
    """One bounded, path-private recovery record for an AppContainer profile."""

    profile_id: str
    profile_name: str
    sid: str
    owner_pid: int
    owner_created_100ns: int
    phase: AclRecoveryPhase
    grant_roots: tuple[Path, ...]
    objects: tuple[AclRecoveryObject, ...]
    peer_sid: str | None = None
    journal_version: int = ACL_RECOVERY_JOURNAL_VERSION

    def __post_init__(self) -> None:
        roots = tuple(os.fspath(path) for path in self.grant_roots)
        object_paths = tuple(os.fspath(item.path) for item in self.objects)
        if (
            type(self.journal_version) is not int
            or self.journal_version
            not in {ACL_RECOVERY_LEGACY_VERSION, ACL_RECOVERY_JOURNAL_VERSION}
            or type(self.profile_id) is not str
            or _PROFILE_ID.fullmatch(self.profile_id) is None
            or type(self.profile_name) is not str
            or _PROFILE_NAME.fullmatch(self.profile_name) is None
            or not self.profile_name.endswith(self.profile_id)
            or type(self.sid) is not str
            or _APP_CONTAINER_SID.fullmatch(self.sid) is None
            or type(self.owner_pid) is not int
            or not 1 <= self.owner_pid <= 0xFFFF_FFFF
            or type(self.owner_created_100ns) is not int
            or not 1 <= self.owner_created_100ns <= 0xFFFF_FFFF_FFFF_FFFF
            or type(self.phase) is not AclRecoveryPhase
            or type(self.grant_roots) is not tuple
            or not 1 <= len(self.grant_roots) <= MAX_ACL_RECOVERY_GRANT_ROOTS
            or any(
                not isinstance(path, Path) or not path.is_absolute() for path in self.grant_roots
            )
            or len(set(roots)) != len(roots)
            or type(self.objects) is not tuple
            or not 1 <= len(self.objects) <= MAX_ACL_RECOVERY_OBJECTS
            or any(type(item) is not AclRecoveryObject for item in self.objects)
            or len(set(object_paths)) != len(object_paths)
            or not set(roots) <= set(object_paths)
        ):
            raise ValueError("invalid ACL recovery entry")
        if self.journal_version == ACL_RECOVERY_LEGACY_VERSION:
            if self.peer_sid is not None or any(
                item.volume_serial is not None
                or item.file_id is not None
                or item.guard_baseline is not None
                for item in self.objects
            ):
                raise ValueError("invalid legacy ACL recovery entry")
            return
        if (
            type(self.peer_sid) is not str
            or _WINDOWS_SID.fullmatch(self.peer_sid) is None
            or any(
                type(item.volume_serial) is not int
                or type(item.file_id) is not str
                or (item.is_directory and item.guard_baseline is not None)
                or (not item.is_directory and type(item.guard_baseline) is not int)
                for item in self.objects
            )
        ):
            raise ValueError("invalid exact-object ACL recovery entry")

    def __repr__(self) -> str:
        return (
            "AclRecoveryEntry("
            f"profile_id={self.profile_id!r}, phase={self.phase.value!r}, "
            f"grant_root_count={len(self.grant_roots)}, object_count={len(self.objects)})"
        )


@dataclass(frozen=True, slots=True)
class AclRecoveryJournalError(RuntimeError):
    """Sanitized persistent-journal failure."""

    context: str

    def __str__(self) -> str:
        return f"ACL recovery journal failed at {self.context}"


def encode_acl_recovery_entry(entry: AclRecoveryEntry) -> bytes:
    """Encode one canonical, bounded current-version record."""

    if type(entry) is not AclRecoveryEntry:
        raise AclRecoveryJournalError("encode.type")
    document: dict[str, object] = {
        "grant_roots": [os.fspath(path) for path in entry.grant_roots],
        "journal_version": entry.journal_version,
        "objects": [],
        "owner_created_100ns": entry.owner_created_100ns,
        "owner_pid": entry.owner_pid,
        "phase": entry.phase.value,
        "profile_id": entry.profile_id,
        "profile_name": entry.profile_name,
        "sid": entry.sid,
    }
    encoded_objects: list[dict[str, object]] = []
    for item in entry.objects:
        encoded: dict[str, object] = {
            "auto_inherited": item.auto_inherited,
            "device": item.device,
            "inode": item.inode,
            "is_directory": item.is_directory,
            "path": os.fspath(item.path),
        }
        if entry.journal_version == ACL_RECOVERY_JOURNAL_VERSION:
            encoded.update(
                {
                    "file_id": item.file_id,
                    "guard_baseline": item.guard_baseline,
                    "volume_serial": item.volume_serial,
                }
            )
        encoded_objects.append(encoded)
    document["objects"] = encoded_objects
    if entry.journal_version == ACL_RECOVERY_JOURNAL_VERSION:
        document["peer_sid"] = entry.peer_sid
    try:
        payload = (
            json.dumps(
                document,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
            + b"\n"
        )
    except (TypeError, ValueError) as error:
        raise AclRecoveryJournalError("encode.value") from error
    if len(payload) > MAX_ACL_RECOVERY_BYTES:
        raise AclRecoveryJournalError("encode.size")
    return payload


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _require_bounded_json_shape(document: object) -> None:
    nodes = 0
    pending: list[tuple[object, int]] = [(document, 0)]
    while pending:
        value, depth = pending.pop()
        nodes += 1
        if nodes > MAX_ACL_RECOVERY_JSON_NODES or depth > MAX_ACL_RECOVERY_JSON_DEPTH:
            raise AclRecoveryJournalError("decode.shape")
        if type(value) is dict:
            pending.extend((item, depth + 1) for item in value.values())
        elif type(value) is list:
            pending.extend((item, depth + 1) for item in value)


def decode_acl_recovery_entry(
    payload: bytes,
    *,
    expected_profile_id: str | None = None,
) -> AclRecoveryEntry:
    """Decode one canonical record without exposing contained paths in failures."""

    if type(payload) is not bytes or not 1 <= len(payload) <= MAX_ACL_RECOVERY_BYTES:
        raise AclRecoveryJournalError("decode.size")
    try:
        document = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("constant")),
        )
    except (UnicodeError, ValueError, json.JSONDecodeError, RecursionError) as error:
        raise AclRecoveryJournalError("decode.json") from error
    _require_bounded_json_shape(document)
    if type(document) is not dict:
        raise AclRecoveryJournalError("decode.fields")
    version = document.get("journal_version")
    if version == ACL_RECOVERY_LEGACY_VERSION:
        expected_fields = _V1_FIELDS
        expected_object_fields = _V1_OBJECT_FIELDS
    elif version == ACL_RECOVERY_JOURNAL_VERSION:
        expected_fields = _V2_FIELDS
        expected_object_fields = _V2_OBJECT_FIELDS
    else:
        raise AclRecoveryJournalError("decode.version")
    if frozenset(document) != expected_fields:
        raise AclRecoveryJournalError("decode.fields")
    roots_value = document["grant_roots"]
    objects_value = document["objects"]
    if (
        type(roots_value) is not list
        or not 1 <= len(roots_value) <= MAX_ACL_RECOVERY_GRANT_ROOTS
        or type(objects_value) is not list
        or not 1 <= len(objects_value) <= MAX_ACL_RECOVERY_OBJECTS
    ):
        raise AclRecoveryJournalError("decode.bounds")
    try:
        roots = tuple(Path(value) for value in roots_value if type(value) is str)
        if len(roots) != len(roots_value):
            raise ValueError("invalid roots")
        objects: list[AclRecoveryObject] = []
        for value in objects_value:
            if type(value) is not dict or frozenset(value) != expected_object_fields:
                raise ValueError("invalid object fields")
            path_value = value["path"]
            if type(path_value) is not str:
                raise ValueError("invalid object path")
            objects.append(
                AclRecoveryObject(
                    path=Path(path_value),
                    device=value["device"],
                    inode=value["inode"],
                    is_directory=value["is_directory"],
                    auto_inherited=value["auto_inherited"],
                    volume_serial=value.get("volume_serial"),
                    file_id=value.get("file_id"),
                    guard_baseline=value.get("guard_baseline"),
                )
            )
        entry = AclRecoveryEntry(
            profile_id=document["profile_id"],
            profile_name=document["profile_name"],
            sid=document["sid"],
            owner_pid=document["owner_pid"],
            owner_created_100ns=document["owner_created_100ns"],
            phase=AclRecoveryPhase(document["phase"]),
            grant_roots=roots,
            objects=tuple(objects),
            peer_sid=document.get("peer_sid"),
            journal_version=document["journal_version"],
        )
    except (ValueError, TypeError) as error:
        raise AclRecoveryJournalError("decode.value") from error
    if expected_profile_id is not None and entry.profile_id != expected_profile_id:
        raise AclRecoveryJournalError("decode.binding")
    if encode_acl_recovery_entry(entry) != payload:
        raise AclRecoveryJournalError("decode.canonical")
    return entry


def ledger_name(profile_id: str) -> str:
    """Return the one canonical final name for a validated profile identifier."""

    if type(profile_id) is not str or _PROFILE_ID.fullmatch(profile_id) is None:
        raise AclRecoveryJournalError("name.profile")
    return f"worker-acl-{profile_id}.json"


def _identity(path: Path) -> tuple[int, int, bool]:
    try:
        observed = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        raise
    except OSError as error:
        raise AclRecoveryJournalError("filesystem.identity") from error
    attributes = int(getattr(observed, "st_file_attributes", 0))
    if (
        attributes & _REPARSE_ATTRIBUTE
        or observed.st_nlink != 1
        or not stat.S_ISREG(observed.st_mode)
        or observed.st_ino < 1
    ):
        raise AclRecoveryJournalError("filesystem.object")
    return (int(observed.st_dev), int(observed.st_ino), stat.S_ISREG(observed.st_mode))


@dataclass(slots=True, repr=False)
class AclRecoveryJournal:
    """Atomic record storage below one separately authenticated private directory."""

    directory: Path

    def __post_init__(self) -> None:
        if not isinstance(self.directory, Path) or not self.directory.is_absolute():
            raise AclRecoveryJournalError("directory")

    def inventory(self) -> tuple[AclRecoveryEntry, ...]:
        """Load every final record and remove only exact unpublished stage files."""

        directory_identity = self._directory_identity()
        entries: list[AclRecoveryEntry] = []
        names: list[str] = []
        try:
            with os.scandir(self.directory) as candidates:
                for item in candidates:
                    if len(names) >= MAX_ACL_RECOVERY_ENTRIES * 3:
                        raise AclRecoveryJournalError("inventory.entries")
                    names.append(item.name)
        except OSError as error:
            raise AclRecoveryJournalError("inventory.scan") from error
        for name in sorted(names):
            ledger_match = _LEDGER_NAME.fullmatch(name)
            stage_match = _STAGE_NAME.fullmatch(name)
            if ledger_match is None and stage_match is None:
                raise AclRecoveryJournalError("inventory.name")
            path = self.directory / name
            if stage_match is not None:
                self._remove_stage(path)
                continue
            if ledger_match is None:
                raise AclRecoveryJournalError("inventory.name")
            if len(entries) >= MAX_ACL_RECOVERY_ENTRIES:
                raise AclRecoveryJournalError("inventory.entries")
            profile_id = ledger_match.group(1)
            entries.append(self._read(path, expected_profile_id=profile_id))
        self._require_directory_identity(directory_identity)
        return tuple(entries)

    def write(self, entry: AclRecoveryEntry) -> None:
        """Flush and atomically publish one phase before its corresponding mutation."""

        payload = encode_acl_recovery_entry(entry)
        directory_identity = self._directory_identity()
        destination = self.directory / ledger_name(entry.profile_id)
        stage = self.directory / (f"worker-acl-{entry.profile_id}.{token_hex(12)}.tmp")
        descriptor: int | None = None
        published = False
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_BINARY"):
                flags |= os.O_BINARY
            descriptor = os.open(stage, flags, 0o600)
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written < 1:
                    raise OSError("short write")
                offset += written
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            _identity(stage)
            self._require_directory_identity(directory_identity)
            os.replace(stage, destination)
            published = True
            if self._read(destination, expected_profile_id=entry.profile_id) != entry:
                raise AclRecoveryJournalError("write.readback")
            self._require_directory_identity(directory_identity)
        except AclRecoveryJournalError:
            raise
        except OSError as error:
            raise AclRecoveryJournalError("write.filesystem") from error
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if not published:
                self._remove_stage(stage, missing_ok=True)

    def delete(self, profile_id: str) -> None:
        """Delete only the exact identity-checked final record."""

        directory_identity = self._directory_identity()
        path = self.directory / ledger_name(profile_id)
        try:
            _identity(path)
            self._require_directory_identity(directory_identity)
            os.unlink(path)
        except FileNotFoundError:
            return
        except AclRecoveryJournalError:
            raise
        except OSError as error:
            raise AclRecoveryJournalError("delete.filesystem") from error
        self._require_directory_identity(directory_identity)

    def exists(self, profile_id: str) -> bool:
        path = self.directory / ledger_name(profile_id)
        try:
            _identity(path)
        except FileNotFoundError:
            return False
        return True

    def _read(self, path: Path, *, expected_profile_id: str) -> AclRecoveryEntry:
        before = _identity(path)
        try:
            with path.open("rb") as stream:
                payload = stream.read(MAX_ACL_RECOVERY_BYTES + 1)
        except OSError as error:
            raise AclRecoveryJournalError("read.filesystem") from error
        if _identity(path) != before:
            raise AclRecoveryJournalError("read.identity")
        return decode_acl_recovery_entry(payload, expected_profile_id=expected_profile_id)

    def _remove_stage(self, path: Path, *, missing_ok: bool = False) -> None:
        try:
            _identity(path)
            os.unlink(path)
        except FileNotFoundError:
            if missing_ok:
                return
            raise AclRecoveryJournalError("stage.missing") from None
        except AclRecoveryJournalError:
            raise
        except OSError as error:
            raise AclRecoveryJournalError("stage.delete") from error

    def _directory_identity(self) -> tuple[int, int]:
        try:
            observed = self.directory.stat(follow_symlinks=False)
        except OSError as error:
            raise AclRecoveryJournalError("directory.identity") from error
        attributes = int(getattr(observed, "st_file_attributes", 0))
        if (
            attributes & _REPARSE_ATTRIBUTE
            or not stat.S_ISDIR(observed.st_mode)
            or observed.st_ino < 1
        ):
            raise AclRecoveryJournalError("directory.object")
        return (int(observed.st_dev), int(observed.st_ino))

    def _require_directory_identity(self, expected: tuple[int, int]) -> None:
        if self._directory_identity() != expected:
            raise AclRecoveryJournalError("directory.changed")

"""Local Windows update coordination under the installed launcher's file lease."""

from __future__ import annotations

import hashlib
import json
import ntpath
import os
import re
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, cast

from paperworkshell.domain.manifest_repository import CURRENT_MANIFEST_SCHEMA_VERSION
from paperworkshell.domain.product_identity import INSTALLED_APPLICATION_ID
from paperworkshell.domain.update_verification import (
    MAX_METADATA_BYTES,
    UpdateMetadata,
    UpdateVerificationError,
    verify_update_metadata,
)
from paperworkshell.storage._source_windows import is_fixed_local_drive
from paperworkshell.storage._windows import (
    WindowsHandle,
    _query_opened_info,
    open_handle,
    require_expected_handle,
)
from paperworkshell.storage.errors import WorkspaceError
from paperworkshell.storage.permissions import WindowsPrivateDirectoryPermissions
from paperworkshell.storage.update_payload import (
    UpdatePayload,
    UpdatePayloadError,
    extract_update_payload,
    validate_installed_update_payload,
)
from paperworkshell.storage.update_staging import (
    UpdateStagingError,
    cleanup_interrupted_update_stages,
    stage_update_bundle,
)
from paperworkshell.storage.update_trust import (
    MAX_TRUST_RECORD_BYTES,
    ChannelTrust,
    TrustAnchor,
    TrustCheckpoint,
    UpdateTrustError,
    verify_channel_trust,
)

_STATE_NAME: Final = "update-state-v1"
_PENDING_STATE_NAME: Final = "update-state-pending-v1.tmp"
_ANCHOR_NAME: Final = "paperworkshell-update-anchor-v1.json"
_STATE_BYTES: Final = 16384
_SLOTS: Final = ("A", "B", "C")
_AUDIT_LIMIT: Final = 16
_MAX_TREE_ENTRIES: Final = 32768
_MAX_SEQUENCE: Final = (1 << 63) - 1
_TERMS: Final = "notices/Microsoft-runtime-recipient-terms.txt"
_VERSION: Final = re.compile(r"(?:0|[1-9][0-9]{0,3})(?:\.(?:0|[1-9][0-9]{0,3})){2}")
_CANDIDATE: Final = re.compile(r"[a-z0-9][a-z0-9.-]{0,95}")
_HASH: Final = re.compile(r"[0-9a-f]{64}")


class UpdateInstallationError(RuntimeError):
    """Path-free failure surfaced by the standalone update window."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class InstallationStatus:
    active_version: str
    active_candidate: str
    can_restore: bool
    has_staged_updates: bool
    trust_available: bool


@dataclass(frozen=True, slots=True)
class UpdateOffer:
    metadata: UpdateMetadata
    _directory: Path = field(repr=False)
    _signed_bytes_sha256: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class _Slot:
    name: str
    version: str
    candidate: str
    sequence: int
    manifest_sha256: str
    inventory_sha256: str


@dataclass(frozen=True, slots=True)
class _State:
    sequence_floor: int = 0
    trust_floor: int = 0
    trust: TrustCheckpoint = field(default_factory=TrustCheckpoint)
    active: _Slot | None = None
    previous: _Slot | None = None
    can_restore: bool = False
    audit: tuple[_Slot, ...] = ()


def _unsafe_state() -> UpdateInstallationError:
    return UpdateInstallationError(
        "UPDATE_STATE_UNAVAILABLE", "PaperWorkShell could not verify its local update state."
    )


def _cancelled() -> UpdateInstallationError:
    return UpdateInstallationError("UPDATE_CANCELLED", "PaperWorkShell update cancelled.")


@contextmanager
def _safe_errors() -> Iterator[None]:
    try:
        yield
    except (
        UpdateVerificationError,
        UpdateTrustError,
        UpdateStagingError,
        UpdatePayloadError,
    ) as error:
        if str(error.code).endswith("_CANCELLED"):
            raise _cancelled() from None
        raise UpdateInstallationError(str(error.code), str(error)) from None
    except OSError, WorkspaceError, subprocess.SubprocessError:
        raise UpdateInstallationError(
            "UPDATE_STORAGE_UNAVAILABLE",
            "PaperWorkShell could not complete the local update operation.",
        ) from None


def _checked_int(value: object) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_SEQUENCE:
        raise _unsafe_state()
    return value


def _text(value: object, pattern: re.Pattern[str]) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise _unsafe_state()
    return value


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise _unsafe_state()
    return result


def _mapping(value: object, fields: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or value.keys() != fields:
        raise _unsafe_state()
    return cast("dict[str, object]", value)


def _parse_slot(raw: object) -> _Slot:
    value = _mapping(
        raw, {"name", "version", "candidate", "sequence", "manifest_sha256", "inventory_sha256"}
    )
    name = value["name"]
    if not isinstance(name, str) or name not in _SLOTS:
        raise _unsafe_state()
    return _Slot(
        name,
        _text(value["version"], _VERSION),
        _text(value["candidate"], _CANDIDATE),
        _checked_int(value["sequence"]),
        _text(value["manifest_sha256"], _HASH),
        _text(value["inventory_sha256"], _HASH),
    )


def _decode_state(raw: bytes) -> _State:
    if raw[1:2] != b"\n":
        raise _unsafe_state()
    try:
        value = _mapping(
            json.loads(raw[2:].decode("utf-8"), object_pairs_hook=_object),
            {
                "sequence_floor",
                "trust_floor",
                "trust",
                "active",
                "previous",
                "can_restore",
                "audit",
            },
        )
        trust = _mapping(value["trust"], {"epoch", "record_sha256"})
        digest = trust["record_sha256"]
        checkpoint = TrustCheckpoint(
            _checked_int(trust["epoch"]), None if digest is None else _text(digest, _HASH)
        )
    except ValueError, RecursionError:
        raise _unsafe_state() from None
    active = None if value["active"] is None else _parse_slot(value["active"])
    previous = None if value["previous"] is None else _parse_slot(value["previous"])
    can_restore = value["can_restore"]
    audit = value["audit"]
    floor = _checked_int(value["sequence_floor"])
    if (
        type(can_restore) is not bool
        or not isinstance(audit, list)
        or len(audit) > _AUDIT_LIMIT
        or raw[:1] != ("0" if active is None else active.name).encode("ascii")
        or (can_restore and active is None)
        or (
            previous is not None
            and (not can_restore or active is None or previous.name == active.name)
        )
    ):
        raise _unsafe_state()
    parsed_audit = tuple(_parse_slot(item) for item in audit)
    if any(
        item is not None and item.sequence > floor for item in (active, previous, *parsed_audit)
    ):
        raise _unsafe_state()
    return _State(
        floor,
        _checked_int(value["trust_floor"]),
        checkpoint,
        active,
        previous,
        can_restore,
        parsed_audit,
    )


def _publish_state(root: Path, state: _State) -> None:
    selector = "0" if state.active is None else state.active.name
    raw = (selector + "\n" + json.dumps(asdict(state), separators=(",", ":"))).encode("ascii")
    if len(raw) > _STATE_BYTES:
        raise _unsafe_state()
    pending = root / _PENDING_STATE_NAME
    if os.path.lexists(pending):
        with open_handle(pending, delete=True, read=True) as handle:
            require_expected_handle(handle, pending, directory=False)
            if handle.info.link_count != 1 or handle.info.logical_size > _STATE_BYTES:
                raise _unsafe_state()
            handle.mark_for_deletion()
    owned = False
    try:
        with pending.open("xb") as stream:
            owned = True
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, root / _STATE_NAME)
    finally:
        if owned:
            pending.unlink(missing_ok=True)


def _pin_local(directory: Path, stack: ExitStack) -> None:
    if (
        not directory.is_absolute()
        or re.fullmatch(r"[A-Za-z]:", directory.drive) is None
        or not 2 <= len(directory.parts) <= 64
        or any(part in {".", ".."} or ntpath.isreserved(part) for part in directory.parts[1:])
        or not is_fixed_local_drive(directory)
    ):
        raise UpdateInstallationError(
            "UPDATE_LOCATION_UNSUPPORTED", "Choose an update folder on a fixed local drive."
        )
    for path in (*reversed(directory.parents), directory):
        handle = stack.enter_context(open_handle(path, traverse=True))
        require_expected_handle(handle, path, directory=True)


def _pinned_file(path: Path, stack: ExitStack) -> WindowsHandle:
    handle = stack.enter_context(open_handle(path, read=True))
    require_expected_handle(handle, path, directory=False)
    if handle.info.link_count != 1 or handle.info.is_sparse:
        raise _unsafe_state()
    return handle


def _small_file(path: Path, maximum: int, *, missing: bool = False) -> bytes | None:
    if missing:
        try:
            path.lstat()
        except FileNotFoundError:
            return None
    with ExitStack() as stack:
        return _pinned_file(path, stack).read_bytes(maximum_bytes=maximum)


def _provisioned_anchor(runtime: Path, channel: object) -> tuple[TrustAnchor | None, int]:
    """Only the independently installed bootstrap provisions a channel root."""
    raw = _small_file(runtime / _ANCHOR_NAME, 4096, missing=True)
    if raw is None:
        return None, 0
    try:
        data = _mapping(
            json.loads(raw.decode("utf-8"), object_pairs_hook=_object),
            {
                "schema_version",
                "channel",
                "root_public_key",
                "minimum_epoch",
                "release_sequence_floor",
            },
        )
        key = data["root_public_key"]
        if (
            type(data["schema_version"]) is not int
            or data["schema_version"] != 1
            or type(channel) is not str
            or data["channel"] != channel
            or type(key) is not str
            or re.fullmatch(r"[0-9a-f]{128}", key) is None
        ):
            raise _unsafe_state()
        return (
            TrustAnchor(channel, bytes.fromhex(key), _checked_int(data["minimum_epoch"])),
            _checked_int(data["release_sequence_floor"]),
        )
    except ValueError, RecursionError:
        raise _unsafe_state() from None


def _private_directory(path: Path) -> None:
    service = WindowsPrivateDirectoryPermissions()
    if os.path.lexists(path):
        service.verify_private_directory(path, context="updates.directory")
    else:
        service.create_private_directory(path, context="updates.directory")


def _remove_slot(path: Path) -> None:
    if not os.path.lexists(path):
        return
    service = WindowsPrivateDirectoryPermissions()
    service.verify_private_directory(path, context="updates.slot")
    count = 0
    pending = [path]
    while pending:
        with os.scandir(pending.pop()) as entries:
            for item in entries:
                info = os.stat(item.path, follow_symlinks=False)
                count += 1
                if (
                    count > _MAX_TREE_ENTRIES
                    or info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
                ):
                    raise _unsafe_state()
                if stat.S_ISDIR(info.st_mode):
                    pending.append(Path(item.path))
                elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise _unsafe_state()
    shutil.rmtree(path)


def _health_check(slot: Path) -> None:
    runtime = slot / "runtime"
    executable = runtime / "PaperWorkShellDesktop.exe"
    result = subprocess.run(
        [
            str(executable),
            "-I",
            "-B",
            "-m",
            "paperworkshell.app.update_release_health",
            str(CURRENT_MANIFEST_SCHEMA_VERSION),
        ],
        cwd=runtime,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=25,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise UpdateInstallationError(
            "UPDATE_RUNTIME_UNAVAILABLE", "The update could not start its staged runtime."
        )


def _require_launcher_lease(root: Path) -> None:
    raw = os.environ.pop("PWS_UPDATE_LEASE", "")
    if re.fullmatch(r"[0-9a-f]{1,16}", raw) is None:
        raise _unsafe_state()
    value = int(raw, 16)
    if not 0 < value < (1 << 63):
        raise _unsafe_state()
    lease = WindowsHandle(value, _query_opened_info(value))
    require_expected_handle(
        lease, root.parent.parent / "PaperWorkShell.lifecycle-v1.lock", directory=False
    )
    if lease.info.link_count != 1:
        raise _unsafe_state()


class UpdateInstallation:
    """Update the fixed installation outside cases while the native launcher holds its lease."""

    def __init__(
        self,
        root: Path,
        *,
        base_version: str,
        base_candidate: str,
        anchor: TrustAnchor | None,
        base_sequence_floor: int = 0,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        check_runtime: Callable[[Path], None] = _health_check,
    ) -> None:
        self._root = root
        self._version = base_version
        self._candidate = base_candidate
        self._anchor = anchor
        self._base_sequence_floor = base_sequence_floor
        self._clock = clock
        self._check_runtime = check_runtime

    @classmethod
    def for_installed_runtime(cls) -> UpdateInstallation:
        executable = Path(sys.executable)
        runtime = executable.parent
        root = runtime.parent
        if (
            os.name != "nt"
            or executable.name != "PaperWorkShellDesktop.exe"
            or runtime.name != "runtime"
            or root.name != "PaperWorkShell"
            or root.parent.name != "Programs"
        ):
            raise _unsafe_state()
        with _safe_errors(), ExitStack() as stack:
            _pin_local(runtime, stack)
            _require_launcher_lease(root)
            raw = _pinned_file(runtime / "paperworkshell-installed-v1.json", stack).read_bytes(
                maximum_bytes=4096
            )
            try:
                marker = _mapping(
                    json.loads(raw.decode("utf-8"), object_pairs_hook=_object),
                    {"schema_version", "application_id", "version", "candidate_id", "channel"},
                )
            except ValueError, RecursionError:
                raise _unsafe_state() from None
            if type(marker["schema_version"]) is not int or marker["schema_version"] != 1:
                raise _unsafe_state()
            if marker["application_id"] != INSTALLED_APPLICATION_ID:
                raise _unsafe_state()
            anchor, sequence_floor = _provisioned_anchor(runtime, marker["channel"])
            return cls(
                root,
                base_version=_text(marker["version"], _VERSION),
                base_candidate=_text(marker["candidate_id"], _CANDIDATE),
                anchor=anchor,
                base_sequence_floor=sequence_floor,
            )

    def _read_state(self) -> _State:
        raw = _small_file(self._root / _STATE_NAME, _STATE_BYTES, missing=True)
        state = _State() if raw is None else _decode_state(raw)
        return replace(state, sequence_floor=max(state.sequence_floor, self._base_sequence_floor))

    def _slot_path(self, name: str) -> Path:
        return self._root / f"update-slot-{name}"

    def _pin_offer_folder(self, directory: Path, stack: ExitStack) -> None:
        if directory.is_relative_to(self._root.parent.parent / "PaperWorkShell"):
            raise UpdateInstallationError(
                "UPDATE_LOCATION_UNSUPPORTED",
                "Choose an update folder outside private PaperWorkShell storage.",
            )
        _pin_local(directory, stack)

    def status(self) -> InstallationStatus:
        with _safe_errors(), ExitStack() as stack:
            _pin_local(self._root, stack)
            state = self._read_state()
            active = state.active
            return InstallationStatus(
                self._version if active is None else active.version,
                self._candidate if active is None else active.candidate,
                state.can_restore,
                os.path.lexists(self._root / "update-transport")
                or any(os.path.lexists(self._slot_path(name)) for name in _SLOTS),
                self._anchor is not None,
            )

    def inspect_offer(self, directory: Path) -> UpdateOffer:
        with _safe_errors(), ExitStack() as stack:
            _pin_local(self._root, stack)
            self._pin_offer_folder(directory, stack)
            state, trust, raw, signature, now = self._read_offer(directory, stack)
            metadata = verify_update_metadata(
                raw,
                signature,
                verify_signature=trust.permit_update_signature,
                expected_channel=trust.channel,
                sequence_floor=max(state.sequence_floor, state.trust_floor),
                now=now,
            )
            return UpdateOffer(metadata, directory, hashlib.sha256(raw).hexdigest())

    def _read_offer(
        self, directory: Path, stack: ExitStack
    ) -> tuple[_State, ChannelTrust, bytes, bytes, datetime]:
        if self._anchor is None:
            raise UpdateInstallationError(
                "UPDATE_TRUST_UNAVAILABLE", "This build has no configured update trust authority."
            )
        state = self._read_state()
        now = self._clock()
        trust = verify_channel_trust(
            _pinned_file(directory / "trust.json", stack).read_bytes(
                maximum_bytes=MAX_TRUST_RECORD_BYTES
            ),
            _pinned_file(directory / "trust.sig", stack).read_bytes(maximum_bytes=64),
            anchor=self._anchor,
            checkpoint=state.trust,
            now=now,
        )
        updated = replace(
            state,
            trust=trust.checkpoint,
            trust_floor=max(state.trust_floor, trust.release_sequence_floor),
        )
        if state != updated:
            _publish_state(self._root, updated)
        if trust.revoked:
            raise UpdateInstallationError(
                "UPDATE_CHANNEL_REVOKED", "Updates for this channel have been suspended."
            )
        return (
            updated,
            trust,
            _pinned_file(directory / "release.json", stack).read_bytes(
                maximum_bytes=MAX_METADATA_BYTES
            ),
            _pinned_file(directory / "release.sig", stack).read_bytes(maximum_bytes=64),
            now,
        )

    def apply_offer(self, offer: UpdateOffer, *, cancelled: Callable[[], bool]) -> None:
        def checkpoint() -> None:
            if cancelled():
                raise _cancelled()

        with _safe_errors(), ExitStack() as stack:
            _pin_local(self._root, stack)
            self._pin_offer_folder(offer._directory, stack)
            state, trust, raw, signature, now = self._read_offer(offer._directory, stack)
            if hashlib.sha256(raw).hexdigest() != offer._signed_bytes_sha256:
                raise UpdateInstallationError(
                    "UPDATE_OFFER_CHANGED", "The update offer changed. Open it again to review it."
                )
            checkpoint()
            package = offer._directory / "runtime.zip"
            _pinned_file(package, stack)
            source = stack.enter_context(package.open("rb"))
            staging_root = self._root / "update-transport"
            _private_directory(staging_root)
            cleanup_interrupted_update_stages(staging_root)
            unused = next(
                name
                for name in _SLOTS
                if all(slot is None or slot.name != name for slot in (state.active, state.previous))
            )
            destination = self._slot_path(unused)
            floor = max(state.sequence_floor, state.trust_floor)
            with (
                stage_update_bundle(
                    staging_root,
                    raw,
                    signature,
                    source,
                    verify_signature=trust.permit_update_signature,
                    expected_channel=trust.channel,
                    sequence_floor=floor,
                    now=now,
                    checkpoint=checkpoint,
                ) as staged,
                staged.open_package() as verified,
            ):
                if staged.metadata != offer.metadata:
                    raise _unsafe_state()
                _remove_slot(destination)
                _private_directory(destination)
                payload = extract_update_payload(
                    verified,
                    destination,
                    staged.metadata,
                    installed_case_schema_version=CURRENT_MANIFEST_SCHEMA_VERSION,
                    checkpoint=checkpoint,
                )
                self._same_recipient_terms(payload)
                checkpoint()
                self._check_runtime(destination)
            checkpoint()
            now = self._clock()
            if not trust.issued_at <= now < trust.expires_at:
                raise UpdateInstallationError(
                    "UPDATE_TRUST_RECORD_NOT_CURRENT", "The update trust information expired."
                )
            verify_update_metadata(
                raw,
                signature,
                verify_signature=trust.permit_update_signature,
                expected_channel=trust.channel,
                sequence_floor=floor,
                now=now,
            )
            checkpoint()
            slot = _Slot(
                unused,
                payload.version,
                payload.candidate_id,
                offer.metadata.sequence,
                payload.manifest_sha256,
                payload.inventory_sha256,
            )
            _publish_state(
                self._root,
                replace(
                    state,
                    sequence_floor=slot.sequence,
                    active=slot,
                    previous=state.active,
                    can_restore=True,
                    audit=(*state.audit, slot)[-_AUDIT_LIMIT:],
                ),
            )

    def _same_recipient_terms(self, payload: UpdatePayload) -> None:
        current = _small_file(self._root / _TERMS, 128 * 1024, missing=True)
        digest = None if current is None else hashlib.sha256(current).hexdigest()
        if digest != payload.recipient_terms_sha256:
            raise UpdateInstallationError(
                "UPDATE_TERMS_CHANGED",
                "The update changes recipient terms. Use the regular installer to review them.",
            )

    def restore_previous(self) -> None:
        with _safe_errors(), ExitStack() as stack:
            _pin_local(self._root, stack)
            state = self._read_state()
            if not state.can_restore:
                raise UpdateInstallationError(
                    "UPDATE_RECOVERY_UNAVAILABLE", "No previous update is available to restore."
                )
            if state.previous is not None:
                destination = self._slot_path(state.previous.name)
                _pin_local(destination, stack)
                validate_installed_update_payload(
                    destination, expected_manifest_sha256=state.previous.manifest_sha256
                )
                self._check_runtime(destination)
            else:
                self._check_runtime(self._root)
            _publish_state(
                self._root, replace(state, active=state.previous, previous=None, can_restore=False)
            )

    def remove_installed_updates(self) -> None:
        with _safe_errors(), ExitStack() as stack:
            _pin_local(self._root, stack)
            state = self._read_state()
            self._check_runtime(self._root)
            _publish_state(
                self._root, replace(state, active=None, previous=None, can_restore=False)
            )
            for name in _SLOTS:
                _remove_slot(self._slot_path(name))
            transport = self._root / "update-transport"
            if os.path.lexists(transport):
                cleanup_interrupted_update_stages(transport)
                transport.rmdir()

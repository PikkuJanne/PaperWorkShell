"""Stage a signed update on a private fixed Windows volume."""

from __future__ import annotations

import ntpath
import os
import re
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Final
from uuid import uuid4

from paperworkshell.domain.update_verification import (
    MAX_PACKAGE_BYTES,
    UpdateErrorCode,
    UpdateMetadata,
    UpdateVerificationError,
    verify_update_bundle,
    verify_update_metadata,
)
from paperworkshell.storage._source_windows import is_fixed_local_drive
from paperworkshell.storage._windows import (
    FileIdentity,
    WindowsHandle,
    open_handle,
    require_expected_handle,
    require_local_acl_volume,
    require_no_alternate_streams,
    verify_private_acl,
)
from paperworkshell.storage.errors import WorkspaceError
from paperworkshell.storage.permissions import WindowsPrivateDirectoryPermissions

_PACKAGE_NAME: Final = "update-package.zip"
_STAGE_NAME: Final = re.compile(r"stage-[0-9a-f]{32}")
_CHUNK_BYTES: Final = 1024 * 1024
_MAX_PATH_PARTS: Final = 64
_MAX_INTERRUPTED_STAGES: Final = 16


class UpdateStagingError(RuntimeError):
    """Fixed update staging failure with no path or operating-system message."""

    code: Final = "UPDATE_STAGING_FAILED"

    def __init__(self) -> None:
        super().__init__("PaperWorkShell could not safely stage the update package.")


@dataclass(frozen=True, slots=True)
class StagedUpdate:
    """A verified staging file whose lifetime is managed by stage_update_bundle."""

    metadata: UpdateMetadata
    package_path: Path = field(repr=False)
    _handle: WindowsHandle = field(repr=False)
    _identity: FileIdentity = field(repr=False)

    def assert_package_identity(self) -> None:
        """Confirm that the guarded local name still denotes the verified package."""
        try:
            self._handle.refresh_info()
            _require_package(
                self._handle, self.package_path, self._identity, self.metadata.package_size
            )
            with open_handle(self.package_path, read=True) as current:
                _require_package(
                    current, self.package_path, self._identity, self.metadata.package_size
                )
        except OSError, WorkspaceError:
            raise UpdateStagingError() from None

    @contextmanager
    def open_package(self) -> Iterator[BinaryIO]:
        """Open a fresh read stream while the package and its ancestors are pinned."""
        self.assert_package_identity()
        try:
            stream = self.package_path.open("rb")
        except OSError:
            raise UpdateStagingError() from None
        with stream:
            yield stream


@contextmanager
def stage_update_bundle(
    staging_root: Path,
    metadata_bytes: bytes,
    signature: bytes,
    package: BinaryIO,
    *,
    verify_signature: Callable[[bytes, bytes], bool],
    expected_channel: str,
    sequence_floor: int,
    now: datetime,
    checkpoint: Callable[[], None] | None = None,
) -> Iterator[StagedUpdate]:
    """Verify a bounded package copied into an existing updater-owned private root.

    The caller supplies a configured root outside case storage and holds its own
    updater lifecycle guard. The path and borrowed streams are usable only inside
    the context. No existing stage is ever resumed or used as an update offer.
    """
    metadata = verify_update_metadata(
        metadata_bytes,
        signature,
        verify_signature=verify_signature,
        expected_channel=expected_channel,
        sequence_floor=sequence_floor,
        now=now,
    )
    with ExitStack() as lifetime:
        try:
            stage = _create_stage_directory(staging_root, lifetime)
            path = stage / _PACKAGE_NAME
            with path.open("xb") as output, open_handle(path) as created:
                identity = created.info.identity
                require_expected_handle(created, path, directory=False)
                lifetime.callback(_delete_package, path, identity)
                _require_package(created, path, identity, 0)
                _copy_package(package, output, metadata.package_size, checkpoint)

            guard = lifetime.enter_context(open_handle(path, read=True))
            staged = StagedUpdate(metadata, path, guard, identity)
            with staged.open_package() as stored:
                verify_update_bundle(
                    metadata_bytes,
                    signature,
                    stored,
                    verify_signature=verify_signature,
                    expected_channel=expected_channel,
                    sequence_floor=sequence_floor,
                    now=now,
                )
        except OSError, WorkspaceError:
            raise UpdateStagingError() from None
        yield staged


def cleanup_interrupted_update_stages(staging_root: Path) -> None:
    """Discard verified stage remnants while the caller holds the exclusive update lease.

    The root must already exist with private permissions. Inspect only the bounded
    set of generated directory names and each directory's one allowed file. Hold
    exact handles throughout and refuse unexpected entries before deleting any stage.
    """
    try:
        with ExitStack() as lifetime:
            _pin_stage_root(staging_root, lifetime)
            stages: list[tuple[WindowsHandle, WindowsHandle | None]] = []
            with os.scandir(staging_root) as entries:
                for count, entry in enumerate(entries, start=1):
                    if count > _MAX_INTERRUPTED_STAGES or _STAGE_NAME.fullmatch(entry.name) is None:
                        raise UpdateStagingError()
                    directory = staging_root / entry.name
                    handle = lifetime.enter_context(open_handle(directory, read=True, delete=True))
                    require_expected_handle(handle, directory, directory=True)
                    verify_private_acl(handle)
                    require_no_alternate_streams(directory)
                    stages.append((handle, _interrupted_package(directory, lifetime)))

            for directory_handle, package_handle in stages:
                if package_handle is not None:
                    package_handle.mark_for_deletion()
                    package_handle.close()
                directory_handle.mark_for_deletion()
                directory_handle.close()
    except OSError, WorkspaceError:
        raise UpdateStagingError() from None


def _interrupted_package(directory: Path, lifetime: ExitStack) -> WindowsHandle | None:
    with os.scandir(directory) as entries:
        entry = next(entries, None)
        if entry is None:
            return None
        if entry.name != _PACKAGE_NAME or next(entries, None) is not None:
            raise UpdateStagingError()
    path = directory / _PACKAGE_NAME
    handle = lifetime.enter_context(open_handle(path, read=True, delete=True))
    info = handle.info
    if not 0 <= info.logical_size <= MAX_PACKAGE_BYTES:
        raise UpdateStagingError()
    _require_package(handle, path, info.identity, info.logical_size)
    return handle


def _pin_stage_root(root: Path, lifetime: ExitStack) -> None:
    if (
        os.name != "nt"
        or not root.is_absolute()
        or re.fullmatch(r"[A-Za-z]:", root.drive) is None
        or not 2 <= len(root.parts) <= _MAX_PATH_PARTS
        or any(part in {".", ".."} or ntpath.isreserved(part) for part in root.parts[1:])
        or not is_fixed_local_drive(root)
    ):
        raise UpdateStagingError()

    # Holding every component prevents a parent rename or write from redirecting
    # later named opens. Check the opened object before traversing the next name.
    for directory in reversed(root.parents):
        handle = lifetime.enter_context(open_handle(directory, read=True))
        require_expected_handle(handle, directory, directory=True)
    root_handle = lifetime.enter_context(open_handle(root, read=True))
    require_expected_handle(root_handle, root, directory=True)
    require_local_acl_volume(root)
    verify_private_acl(root_handle)
    require_no_alternate_streams(root)


def _create_stage_directory(root: Path, lifetime: ExitStack) -> Path:
    _pin_stage_root(root, lifetime)
    stage = root / f"stage-{uuid4().hex}"
    permission = WindowsPrivateDirectoryPermissions().create_private_directory(
        stage, context="update.stage"
    )
    stage_handle = lifetime.enter_context(open_handle(stage, read=True, delete=True))
    require_expected_handle(
        stage_handle, stage, directory=True, expected_identity=permission.identity
    )
    lifetime.callback(_delete_stage, stage_handle)
    verify_private_acl(stage_handle)
    return stage


def _require_package(handle: WindowsHandle, path: Path, identity: FileIdentity, size: int) -> None:
    require_expected_handle(handle, path, directory=False, expected_identity=identity)
    info = handle.info
    if info.is_sparse or info.link_count != 1 or info.logical_size != size:
        raise UpdateStagingError()
    require_no_alternate_streams(path)


def _copy_package(
    source: BinaryIO,
    output: BinaryIO,
    expected_size: int,
    checkpoint: Callable[[], None] | None,
) -> None:
    remaining = expected_size
    while True:
        if checkpoint is not None:
            checkpoint()
        limit = min(_CHUNK_BYTES, remaining + 1)
        try:
            chunk = source.read(limit)
        except OSError, ValueError:
            raise UpdateVerificationError(UpdateErrorCode.PACKAGE_UNREADABLE) from None
        if type(chunk) is not bytes:
            raise UpdateVerificationError(UpdateErrorCode.PACKAGE_UNREADABLE)
        if len(chunk) > min(limit, remaining):
            raise UpdateVerificationError(UpdateErrorCode.PACKAGE_MISMATCH)
        if not chunk:
            break
        if output.write(chunk) != len(chunk):
            raise UpdateStagingError()
        remaining -= len(chunk)
    if remaining:
        raise UpdateVerificationError(UpdateErrorCode.PACKAGE_MISMATCH)
    output.flush()
    os.fsync(output.fileno())


def _delete_package(path: Path, identity: FileIdentity) -> None:
    try:
        with open_handle(path, delete=True) as handle:
            require_expected_handle(handle, path, directory=False, expected_identity=identity)
            handle.mark_for_deletion()
    except OSError, WorkspaceError:
        raise UpdateStagingError() from None


def _delete_stage(handle: WindowsHandle) -> None:
    try:
        # Removing the authenticated empty directory is intentionally nonrecursive.
        handle.mark_for_deletion()
    except OSError, WorkspaceError:
        raise UpdateStagingError() from None

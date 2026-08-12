"""Typed, fail-closed storage seam for worker-private temporary files."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Protocol, cast

WORKER_PRIVATE_TEMP_MINIMUM_FREE_SPACE_RESERVE_BYTES = 256 * 1024 * 1024
_MAXIMUM_SAFE_CAPACITY_BYTES = (1 << 63) - 1


class WorkerPrivateTempStorageError(OSError):
    """Content-free signal mapped to an operation's stable resource error."""

    def __init__(self) -> None:
        super().__init__()


class WorkerPrivateTempStorage(Protocol):
    """Capacity and file-creation authority for one authenticated temp root."""

    def require_capacity(self, root: Path, byte_count: int) -> None: ...

    def open_anonymous(self, root: Path) -> BinaryIO: ...

    def open_named(self, root: Path, *, suffix: str) -> tuple[BinaryIO, Path]: ...


def _available_bytes(root: Path) -> int:
    return shutil.disk_usage(root).free


@dataclass(frozen=True, slots=True, repr=False)
class OsWorkerPrivateTempStorage:
    """Use the OS volume probe and exclusive files under an authenticated root."""

    available_bytes_probe: Callable[[Path], int] = field(
        default=_available_bytes,
        repr=False,
    )
    minimum_free_space_reserve_bytes: int = WORKER_PRIVATE_TEMP_MINIMUM_FREE_SPACE_RESERVE_BYTES

    def __post_init__(self) -> None:
        if (
            not callable(self.available_bytes_probe)
            or type(self.minimum_free_space_reserve_bytes) is not int
            or not 0 <= self.minimum_free_space_reserve_bytes <= _MAXIMUM_SAFE_CAPACITY_BYTES
        ):
            raise WorkerPrivateTempStorageError

    def require_capacity(self, root: Path, byte_count: int) -> None:
        """Reserve prospectively by requiring the complete operation bound to fit."""

        if not isinstance(root, Path) or not root.is_absolute():
            raise WorkerPrivateTempStorageError
        if (
            type(byte_count) is not int
            or byte_count < 1
            or byte_count > _MAXIMUM_SAFE_CAPACITY_BYTES - self.minimum_free_space_reserve_bytes
        ):
            raise WorkerPrivateTempStorageError
        try:
            available = self.available_bytes_probe(root)
        except Exception:
            raise WorkerPrivateTempStorageError from None
        required = byte_count + self.minimum_free_space_reserve_bytes
        if type(available) is not int or available < required:
            raise WorkerPrivateTempStorageError from None

    def open_anonymous(self, root: Path) -> BinaryIO:
        try:
            return cast(BinaryIO, tempfile.TemporaryFile(mode="w+b", dir=root))
        except OSError:
            raise WorkerPrivateTempStorageError from None

    def open_named(self, root: Path, *, suffix: str) -> tuple[BinaryIO, Path]:
        descriptor: int | None = None
        path: Path | None = None
        try:
            descriptor, name = tempfile.mkstemp(suffix=suffix, dir=root)
            path = Path(name)
            stream = os.fdopen(descriptor, "w+b", buffering=0, closefd=True)
            descriptor = None
            return stream, path
        except OSError:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise WorkerPrivateTempStorageError from None

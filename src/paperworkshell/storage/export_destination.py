"""Authenticated, no-replace Windows publication for verified export bytes.

This module is deliberately narrower than a general filesystem writer.  It accepts
one already-bounded byte source, stages it under an opaque same-directory name,
verifies the retained handle, and publishes that exact identity without replacing
anything at the requested destination.
"""

from __future__ import annotations

import ctypes
import hashlib
import hmac
import os
import re
import unicodedata
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PureWindowsPath
from typing import Final, Never, Protocol
from uuid import UUID, uuid4

from paperworkshell.storage._windows import (
    FileIdentity,
    WindowsHandle,
    _query_opened_info,
    open_handle,
    rename_open_file_no_replace_at,
    require_expected_handle,
    require_local_acl_volume,
    require_no_alternate_streams,
)
from paperworkshell.storage.errors import WorkspaceError

_INVALID_HANDLE_VALUE: Final = ctypes.c_void_p(-1).value

_DELETE: Final = 0x0001_0000
_READ_CONTROL: Final = 0x0002_0000
_SYNCHRONIZE: Final = 0x0010_0000
_FILE_READ_ATTRIBUTES: Final = 0x0000_0080
_GENERIC_READ: Final = 0x8000_0000
_GENERIC_WRITE: Final = 0x4000_0000
_FILE_SHARE_READ: Final = 0x0000_0001
_CREATE_NEW: Final = 1
_FILE_ATTRIBUTE_NORMAL: Final = 0x0000_0080
_FILE_FLAG_SEQUENTIAL_SCAN: Final = 0x0800_0000
_FILE_DISPOSITION_INFO_CLASS: Final = 4

_ERROR_ACCESS_DENIED: Final = 5
_ERROR_WRITE_PROTECT: Final = 19
_ERROR_HANDLE_DISK_FULL: Final = 39
_ERROR_DISK_FULL: Final = 112
_ERROR_FILE_EXISTS: Final = 80
_ERROR_ALREADY_EXISTS: Final = 183

MAX_EXPORT_DESTINATION_BYTES: Final = 9_007_199_254_740_991
MAX_EXPORT_DESTINATION_CHUNK_BYTES: Final = 1024 * 1024
MAX_EXPORT_DESTINATION_PATH_CODE_UNITS: Final = 32_767
MAX_EXPORT_DESTINATION_NAME_CODE_UNITS: Final = 180
MAX_EXPORT_STAGE_ATTEMPTS: Final = 32

_SHA256_RE: Final = re.compile(r"[0-9a-f]{64}\Z")
_STAGE_NAME_RE: Final = re.compile(
    r"\.[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-"
    r"[0-9a-f]{12}\.pws-export\.tmp\Z"
)
_WINDOWS_FORBIDDEN_CHARACTERS: Final = frozenset('<>:"/\\|?*')
_WINDOWS_RESERVED_STEMS: Final = frozenset(
    {
        "aux",
        "clock$",
        "con",
        "conin$",
        "conout$",
        "nul",
        "prn",
        *(f"com{number}" for number in range(1, 10)),
        *(f"lpt{number}" for number in range(1, 10)),
        "com¹",
        "com²",
        "com³",
        "lpt¹",
        "lpt²",
        "lpt³",
    }
)


class ExportDestinationErrorCode(StrEnum):
    """Closed, non-disclosing destination failure registry."""

    MODEL_INVALID = "EXPORT_DESTINATION_MODEL_INVALID"
    PLATFORM_UNSUPPORTED = "EXPORT_DESTINATION_PLATFORM_UNSUPPORTED"
    PATH_INVALID = "EXPORT_DESTINATION_PATH_INVALID"
    LOCATION_UNSUPPORTED = "EXPORT_DESTINATION_LOCATION_UNSUPPORTED"
    NAME_INVALID = "EXPORT_DESTINATION_NAME_INVALID"
    PARENT_UNAVAILABLE = "EXPORT_DESTINATION_PARENT_UNAVAILABLE"
    DESTINATION_UNSAFE = "EXPORT_DESTINATION_UNSAFE"
    SOURCE_INVALID = "EXPORT_DESTINATION_SOURCE_INVALID"
    SOURCE_MISMATCH = "EXPORT_DESTINATION_SOURCE_MISMATCH"
    CANCELLED = "EXPORT_DESTINATION_CANCELLED"
    STAGE_CREATE_FAILED = "EXPORT_DESTINATION_STAGE_CREATE_FAILED"
    STAGE_CLEANUP_FAILED = "EXPORT_DESTINATION_STAGE_CLEANUP_FAILED"
    DESTINATION_DENIED = "EXPORT_DESTINATION_DENIED"
    DESTINATION_FULL = "EXPORT_DESTINATION_FULL"
    WRITE_FAILED = "EXPORT_DESTINATION_WRITE_FAILED"
    FLUSH_FAILED = "EXPORT_DESTINATION_FLUSH_FAILED"
    VERIFY_FAILED = "EXPORT_DESTINATION_VERIFY_FAILED"
    COMMIT_FAILED = "EXPORT_DESTINATION_COMMIT_FAILED"
    INTERNAL_ERROR = "EXPORT_DESTINATION_INTERNAL_ERROR"


_SAFE_ERROR_MESSAGES: Final[dict[ExportDestinationErrorCode, str]] = {
    ExportDestinationErrorCode.MODEL_INVALID: (
        "PaperWorkShell received invalid export-destination data."
    ),
    ExportDestinationErrorCode.PLATFORM_UNSUPPORTED: (
        "Verified export publication requires the supported Windows storage boundary."
    ),
    ExportDestinationErrorCode.PATH_INVALID: (
        "Choose an absolute local destination with a safe Windows path."
    ),
    ExportDestinationErrorCode.LOCATION_UNSUPPORTED: (
        "Choose a destination on a supported fixed local Windows volume."
    ),
    ExportDestinationErrorCode.NAME_INVALID: (
        "Choose a safe, non-reserved Windows filename for the export."
    ),
    ExportDestinationErrorCode.PARENT_UNAVAILABLE: (
        "The selected destination folder is not safely available."
    ),
    ExportDestinationErrorCode.DESTINATION_UNSAFE: (
        "The selected destination is redirected or otherwise unsafe."
    ),
    ExportDestinationErrorCode.SOURCE_INVALID: (
        "The verified export bytes could not be read safely."
    ),
    ExportDestinationErrorCode.SOURCE_MISMATCH: (
        "The export bytes changed before destination publication."
    ),
    ExportDestinationErrorCode.CANCELLED: (
        "Export destination publication was cancelled before commit."
    ),
    ExportDestinationErrorCode.STAGE_CREATE_FAILED: (
        "PaperWorkShell could not create a private export stage in the destination folder."
    ),
    ExportDestinationErrorCode.STAGE_CLEANUP_FAILED: (
        "PaperWorkShell could not confirm cleanup of the private export stage."
    ),
    ExportDestinationErrorCode.DESTINATION_DENIED: (
        "PaperWorkShell does not have permission to write to the selected destination."
    ),
    ExportDestinationErrorCode.DESTINATION_FULL: (
        "The selected destination does not have enough available storage."
    ),
    ExportDestinationErrorCode.WRITE_FAILED: (
        "PaperWorkShell could not write the export destination safely."
    ),
    ExportDestinationErrorCode.FLUSH_FAILED: (
        "PaperWorkShell could not flush the staged export safely."
    ),
    ExportDestinationErrorCode.VERIFY_FAILED: (
        "The staged export could not be verified and was not published."
    ),
    ExportDestinationErrorCode.COMMIT_FAILED: (
        "PaperWorkShell could not atomically publish the verified export."
    ),
    ExportDestinationErrorCode.INTERNAL_ERROR: (
        "PaperWorkShell could not complete export destination publication safely."
    ),
}


@dataclass(slots=True, repr=False)
class ExportDestinationError(ValueError):
    """Stable failure that never includes destination paths or source values."""

    code: ExportDestinationErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not ExportDestinationErrorCode:
            self.code = ExportDestinationErrorCode.INTERNAL_ERROR
        if (
            type(self.context) is not str
            or re.fullmatch(r"[a-z][a-z0-9_.]{0,63}", self.context) is None
        ):
            self.context = "export.destination"
        self.safe_message = _SAFE_ERROR_MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.safe_message)

    def __repr__(self) -> str:
        return f"ExportDestinationError(code={self.code.value!r}, context={self.context!r})"

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject_export_destination(code: ExportDestinationErrorCode, context: str) -> Never:
    """Raise one closed export-destination failure without exception chaining."""

    error = ExportDestinationError(code, context)
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None


class ExportCommitState(StrEnum):
    """Identity-resolved outcome of one no-replace publication attempt."""

    COMMITTED = "committed"
    NOT_COMMITTED = "not_committed"
    DESTINATION_EXISTS = "destination_exists"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True, repr=False)
class ExportDestinationResult:
    """Path-free publication result; output digest remains hidden from representations."""

    state: ExportCommitState
    byte_size: int | None = None
    sha256: str | None = field(default=None, repr=False)
    recovered_outcome: bool = False

    def __post_init__(self) -> None:
        if type(self.state) is not ExportCommitState or type(self.recovered_outcome) is not bool:
            reject_export_destination(
                ExportDestinationErrorCode.MODEL_INVALID,
                "export.result",
            )
        has_artifact = self.state is ExportCommitState.COMMITTED
        if has_artifact:
            if (
                type(self.byte_size) is not int
                or not 1 <= self.byte_size <= MAX_EXPORT_DESTINATION_BYTES
                or type(self.sha256) is not str
                or _SHA256_RE.fullmatch(self.sha256) is None
            ):
                reject_export_destination(
                    ExportDestinationErrorCode.MODEL_INVALID,
                    "export.result_artifact",
                )
        elif self.byte_size is not None or self.sha256 is not None:
            reject_export_destination(
                ExportDestinationErrorCode.MODEL_INVALID,
                "export.result_empty",
            )

    @property
    def committed(self) -> bool:
        return self.state is ExportCommitState.COMMITTED

    def __repr__(self) -> str:
        return (
            "ExportDestinationResult("
            f"state={self.state.value!r}, byte_size={self.byte_size!r}, "
            f"recovered_outcome={self.recovered_outcome!r})"
        )


class ExportDestinationPhase(StrEnum):
    """Stable dependency-injection checkpoints for failure-path verification."""

    BEFORE_PARENT_OPEN = "before_parent_open"
    BEFORE_STAGE_CREATE = "before_stage_create"
    BEFORE_WRITE = "before_write"
    BEFORE_FLUSH = "before_flush"
    BEFORE_READBACK = "before_readback"
    BEFORE_RENAME = "before_rename"


class BoundedExportSource(Protocol):
    """Caller-owned source read only through exact bounded requests."""

    def read(self, size: int, /) -> bytes: ...


Checkpoint = Callable[[ExportDestinationPhase], None]
StageCreator = Callable[[Path], WindowsHandle]
StageWriter = Callable[[WindowsHandle, bytes], int]
StageFlusher = Callable[[WindowsHandle], None]
StageRenamer = Callable[[WindowsHandle, WindowsHandle, str], tuple[bool, int]]
DeletionSetter = Callable[[WindowsHandle, bool], None]
CommittedOpener = Callable[[Path], WindowsHandle]
CommittedReader = Callable[[WindowsHandle, int], bytes]
CommittedCloser = Callable[[WindowsHandle], None]


def _no_checkpoint(_phase: ExportDestinationPhase) -> None:
    return


@dataclass(frozen=True, slots=True)
class ExportDestinationDependencies:
    """Narrow injectable Win32 seams used to prove destructive failure paths."""

    id_factory: Callable[[], UUID] = uuid4
    checkpoint: Checkpoint = _no_checkpoint
    create_stage: StageCreator | None = field(default=None, repr=False)
    write_stage: StageWriter | None = field(default=None, repr=False)
    flush_stage: StageFlusher | None = field(default=None, repr=False)
    rename_stage: StageRenamer | None = field(default=None, repr=False)
    set_deletion: DeletionSetter | None = field(default=None, repr=False)
    open_committed: CommittedOpener | None = field(default=None, repr=False)
    read_committed: CommittedReader | None = field(default=None, repr=False)
    close_committed: CommittedCloser | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        values = (
            self.id_factory,
            self.checkpoint,
            self.create_stage,
            self.write_stage,
            self.flush_stage,
            self.rename_stage,
            self.set_deletion,
            self.open_committed,
            self.read_committed,
            self.close_committed,
        )
        if any(value is not None and not callable(value) for value in values):
            reject_export_destination(
                ExportDestinationErrorCode.MODEL_INVALID,
                "export.dependencies",
            )


class WindowsExportDestination:
    """Publish one verified byte stream to one exact local Windows destination."""

    __slots__ = ("_dependencies",)

    def __init__(self, dependencies: ExportDestinationDependencies | None = None) -> None:
        if dependencies is not None and type(dependencies) is not ExportDestinationDependencies:
            reject_export_destination(
                ExportDestinationErrorCode.MODEL_INVALID,
                "export.dependencies",
            )
        self._dependencies = dependencies or ExportDestinationDependencies()

    def __repr__(self) -> str:
        return "WindowsExportDestination()"

    def commit(
        self,
        destination: Path,
        source: BoundedExportSource,
        *,
        expected_byte_size: int,
        expected_sha256: str,
        cancellation: Callable[[], bool],
    ) -> ExportDestinationResult:
        """Stage, verify, and atomically publish without replacing an existing target."""

        selected = _validate_destination(destination)
        _validate_source_contract(
            source,
            expected_byte_size=expected_byte_size,
            expected_sha256=expected_sha256,
            cancellation=cancellation,
        )
        if os.name != "nt":
            reject_export_destination(
                ExportDestinationErrorCode.PLATFORM_UNSUPPORTED,
                "export.platform",
            )

        try:
            self._checkpoint(ExportDestinationPhase.BEFORE_PARENT_OPEN)
            require_local_acl_volume(selected.parent)
            with open_handle(selected.parent, traverse=True) as parent:
                _authenticate_parent(parent, selected.parent)
                existing = _existing_destination_state(selected)
                if existing is not None:
                    return existing
                return self._stage_and_commit(
                    parent,
                    selected,
                    source,
                    expected_byte_size=expected_byte_size,
                    expected_sha256=expected_sha256,
                    cancellation=cancellation,
                )
        except ExportDestinationError:
            raise
        except WorkspaceError as error:
            _map_workspace_error(error)
        except PermissionError:
            reject_export_destination(
                ExportDestinationErrorCode.DESTINATION_DENIED,
                "export.permission",
            )
        except OSError as error:
            _map_os_error(error, context="export.storage")
        except Exception:
            reject_export_destination(
                ExportDestinationErrorCode.INTERNAL_ERROR,
                "export.storage",
            )

    def _stage_and_commit(
        self,
        parent: WindowsHandle,
        destination: Path,
        source: BoundedExportSource,
        *,
        expected_byte_size: int,
        expected_sha256: str,
        cancellation: Callable[[], bool],
    ) -> ExportDestinationResult:
        stage: WindowsHandle | None = None
        stage_path: Path | None = None
        cleanup_stage = True
        try:
            stage, stage_path = self._create_unique_stage(parent, destination.parent)
            initial = stage.info
            _stream_and_verify(
                stage,
                stage_path,
                source,
                expected_byte_size=expected_byte_size,
                expected_sha256=expected_sha256,
                cancellation=cancellation,
                checkpoint=self._checkpoint,
                write_stage=self._dependencies.write_stage or _write_stage_once,
                flush_stage=self._dependencies.flush_stage or _flush_stage,
            )
            _require_not_cancelled(cancellation)
            _authenticate_parent(parent, destination.parent)
            self._checkpoint(ExportDestinationPhase.BEFORE_RENAME)
            rename_stage = self._dependencies.rename_stage or _rename_stage_no_replace
            delayed = False
            try:
                renamed, _error_number = rename_stage(stage, parent, destination.name)
            except BaseException:
                renamed = False
                delayed = True

            try:
                _authenticate_parent(parent, destination.parent)
            except BaseException:
                # The retained stage identity remains authoritative even if a
                # post-rename parent refresh is interrupted or unavailable.
                delayed = True

            state = _resolve_commit_state(
                stage,
                stage_path=stage_path,
                destination=destination,
                expected_identity=initial.identity,
            )
            if renamed and state is not ExportCommitState.COMMITTED:
                state = ExportCommitState.UNKNOWN
            if state is not ExportCommitState.COMMITTED:
                if state is ExportCommitState.UNKNOWN:
                    # The exact identity may already be the committed final.
                    # Preserve it rather than risk deleting a successful export.
                    cleanup_stage = False
                return ExportDestinationResult(state, recovered_outcome=delayed)

            cleanup_stage = False
            try:
                _require_committed_identity(
                    stage,
                    destination,
                    expected_identity=initial.identity,
                    expected_byte_size=expected_byte_size,
                )
                reopened = _verify_committed_reopen(
                    destination,
                    expected_identity=initial.identity,
                    expected_byte_size=expected_byte_size,
                    expected_sha256=expected_sha256,
                    open_committed=(
                        self._dependencies.open_committed or _open_committed_destination
                    ),
                    read_committed=(self._dependencies.read_committed or _read_committed_chunk),
                    close_committed=(
                        self._dependencies.close_committed or _close_committed_destination
                    ),
                )
            except BaseException:
                # The exact identity is known to have reached the final name,
                # so an indeterminate verification must preserve, never delete.
                return ExportDestinationResult(
                    ExportCommitState.UNKNOWN,
                    recovered_outcome=True,
                )
            if not reopened:
                return ExportDestinationResult(
                    ExportCommitState.UNKNOWN,
                    recovered_outcome=True,
                )

            return ExportDestinationResult(
                ExportCommitState.COMMITTED,
                expected_byte_size,
                expected_sha256,
                recovered_outcome=delayed or not renamed,
            )
        finally:
            if stage is not None:
                _finish_stage(
                    stage,
                    cleanup=cleanup_stage,
                    set_deletion=(self._dependencies.set_deletion or _set_deletion_disposition),
                )

    def _create_unique_stage(
        self,
        parent: WindowsHandle,
        parent_path: Path,
    ) -> tuple[WindowsHandle, Path]:
        creator = self._dependencies.create_stage or _create_exclusive_stage
        for _ in range(MAX_EXPORT_STAGE_ATTEMPTS):
            self._checkpoint(ExportDestinationPhase.BEFORE_STAGE_CREATE)
            try:
                identifier = self._dependencies.id_factory()
            except BaseException:
                reject_export_destination(
                    ExportDestinationErrorCode.STAGE_CREATE_FAILED,
                    "export.stage_identifier",
                )
            if type(identifier) is not UUID or identifier.version != 4:
                reject_export_destination(
                    ExportDestinationErrorCode.STAGE_CREATE_FAILED,
                    "export.stage_identifier",
                )
            stage_path = parent_path / f".{identifier}.pws-export.tmp"
            try:
                stage = creator(stage_path)
            except PermissionError:
                reject_export_destination(
                    ExportDestinationErrorCode.DESTINATION_DENIED,
                    "export.stage_create",
                )
            except OSError as error:
                if _error_number(error) in {_ERROR_FILE_EXISTS, _ERROR_ALREADY_EXISTS}:
                    continue
                _map_os_error(error, context="export.stage_create")
            except ExportDestinationError:
                raise
            except Exception:
                reject_export_destination(
                    ExportDestinationErrorCode.STAGE_CREATE_FAILED,
                    "export.stage_create",
                )
            try:
                _authenticate_parent(parent, parent_path)
                _authenticate_stage(stage, stage_path, parent)
            except BaseException:
                _finish_stage(
                    stage,
                    cleanup=True,
                    set_deletion=(self._dependencies.set_deletion or _set_deletion_disposition),
                )
                raise
            return stage, stage_path
        reject_export_destination(
            ExportDestinationErrorCode.STAGE_CREATE_FAILED,
            "export.stage_collision",
        )

    def _checkpoint(self, phase: ExportDestinationPhase) -> None:
        try:
            self._dependencies.checkpoint(phase)
        except ExportDestinationError:
            raise
        except PermissionError:
            reject_export_destination(
                ExportDestinationErrorCode.DESTINATION_DENIED,
                "export.checkpoint",
            )
        except OSError as error:
            _map_os_error(error, context="export.checkpoint")
        except Exception:
            reject_export_destination(
                ExportDestinationErrorCode.INTERNAL_ERROR,
                "export.checkpoint",
            )


def _validate_destination(value: Path) -> Path:
    if not isinstance(value, Path):
        reject_export_destination(ExportDestinationErrorCode.PATH_INVALID, "export.path")
    raw = os.fspath(value)
    if type(raw) is not str or not raw or "\x00" in raw:
        reject_export_destination(ExportDestinationErrorCode.PATH_INVALID, "export.path")
    try:
        code_units = len(raw.encode("utf-16-le")) // 2
    except UnicodeEncodeError:
        reject_export_destination(ExportDestinationErrorCode.PATH_INVALID, "export.path")
    windows_text = raw.replace("/", "\\")
    lowered = windows_text.casefold()
    if (
        code_units > MAX_EXPORT_DESTINATION_PATH_CODE_UNITS
        or windows_text.startswith("\\\\")
        or lowered.startswith(("\\\\?\\", "\\\\.\\", "\\??\\"))
        or "globalroot" in lowered.split("\\")
    ):
        reject_export_destination(
            ExportDestinationErrorCode.LOCATION_UNSUPPORTED,
            "export.namespace",
        )
    parsed = PureWindowsPath(windows_text)
    if (
        not parsed.is_absolute()
        or re.fullmatch(r"[A-Za-z]:", parsed.drive) is None
        or parsed.root != "\\"
        or ":" in windows_text[2:]
    ):
        reject_export_destination(ExportDestinationErrorCode.PATH_INVALID, "export.absolute")
    components = windows_text[3:].split("\\") if len(windows_text) > 3 else []
    if not components or any(component in {"", ".", ".."} for component in components):
        reject_export_destination(ExportDestinationErrorCode.PATH_INVALID, "export.components")
    for component in components[:-1]:
        _validate_component(component, final=False)
    _validate_component(components[-1], final=True)
    return Path(os.path.abspath(value))


def _validate_component(value: str, *, final: bool) -> None:
    invalid = (
        not value
        or value in {".", ".."}
        or value.endswith((".", " "))
        or any(character in _WINDOWS_FORBIDDEN_CHARACTERS for character in value)
        or any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value)
        or value != unicodedata.normalize("NFC", value)
    )
    try:
        units = len(value.encode("utf-16-le")) // 2
    except UnicodeEncodeError:
        invalid = True
        units = MAX_EXPORT_DESTINATION_PATH_CODE_UNITS + 1
    limit = MAX_EXPORT_DESTINATION_NAME_CODE_UNITS if final else 240
    stem = unicodedata.normalize("NFC", value).casefold().split(".", maxsplit=1)[0].rstrip(" .")
    if invalid or units > limit or stem in _WINDOWS_RESERVED_STEMS:
        reject_export_destination(
            ExportDestinationErrorCode.NAME_INVALID
            if final
            else ExportDestinationErrorCode.PATH_INVALID,
            "export.name" if final else "export.parent_name",
        )


def _validate_source_contract(
    source: BoundedExportSource,
    *,
    expected_byte_size: int,
    expected_sha256: str,
    cancellation: Callable[[], bool],
) -> None:
    if (
        not callable(getattr(source, "read", None))
        or type(expected_byte_size) is not int
        or not 1 <= expected_byte_size <= MAX_EXPORT_DESTINATION_BYTES
        or type(expected_sha256) is not str
        or _SHA256_RE.fullmatch(expected_sha256) is None
        or not callable(cancellation)
    ):
        reject_export_destination(ExportDestinationErrorCode.MODEL_INVALID, "export.source")


def _authenticate_parent(handle: WindowsHandle, expected_path: Path) -> None:
    previous = handle.info
    try:
        current = handle.refresh_info()
        if current.is_reparse_point:
            reject_export_destination(
                ExportDestinationErrorCode.DESTINATION_UNSAFE,
                "export.parent_reparse",
            )
        require_expected_handle(
            handle, expected_path, directory=True, expected_identity=previous.identity
        )
    except ExportDestinationError:
        raise
    except WorkspaceError:
        reject_export_destination(
            ExportDestinationErrorCode.PARENT_UNAVAILABLE,
            "export.parent_identity",
        )


def _authenticate_stage(stage: WindowsHandle, expected_path: Path, parent: WindowsHandle) -> None:
    try:
        require_expected_handle(stage, expected_path, directory=False)
        current = stage.refresh_info()
        if (
            current.is_reparse_point
            or current.is_sparse
            or current.link_count != 1
            or _normalized(current.final_path.parent) != _normalized(parent.info.final_path)
        ):
            reject_export_destination(
                ExportDestinationErrorCode.DESTINATION_UNSAFE,
                "export.stage_identity",
            )
        require_no_alternate_streams(current.final_path)
    except ExportDestinationError:
        raise
    except WorkspaceError:
        reject_export_destination(
            ExportDestinationErrorCode.DESTINATION_UNSAFE,
            "export.stage_identity",
        )


def _existing_destination_state(destination: Path) -> ExportDestinationResult | None:
    if not os.path.lexists(destination):
        return None
    try:
        with open_handle(destination) as existing:
            require_expected_handle(existing, destination, directory=False)
            current = existing.refresh_info()
            if current.is_reparse_point:
                reject_export_destination(
                    ExportDestinationErrorCode.DESTINATION_UNSAFE,
                    "export.final_reparse",
                )
            require_no_alternate_streams(current.final_path)
    except ExportDestinationError:
        raise
    except WorkspaceError:
        reject_export_destination(
            ExportDestinationErrorCode.DESTINATION_UNSAFE,
            "export.final_identity",
        )
    return ExportDestinationResult(ExportCommitState.DESTINATION_EXISTS)


def _stream_and_verify(
    stage: WindowsHandle,
    stage_path: Path,
    source: BoundedExportSource,
    *,
    expected_byte_size: int,
    expected_sha256: str,
    cancellation: Callable[[], bool],
    checkpoint: Checkpoint,
    write_stage: StageWriter,
    flush_stage: StageFlusher,
) -> None:
    digest = hashlib.sha256()
    completed = 0
    while completed < expected_byte_size:
        _require_not_cancelled(cancellation)
        checkpoint(ExportDestinationPhase.BEFORE_WRITE)
        requested = min(MAX_EXPORT_DESTINATION_CHUNK_BYTES, expected_byte_size - completed)
        try:
            chunk = source.read(requested)
        except Exception:
            reject_export_destination(
                ExportDestinationErrorCode.SOURCE_INVALID,
                "export.source_read",
            )
        if type(chunk) is not bytes or not chunk or len(chunk) > requested:
            reject_export_destination(
                ExportDestinationErrorCode.SOURCE_INVALID,
                "export.source_read",
            )
        digest.update(chunk)
        offset = 0
        while offset < len(chunk):
            _require_not_cancelled(cancellation)
            try:
                written = write_stage(stage, chunk[offset:])
            except OSError as error:
                _map_os_error(error, context="export.write")
            except ExportDestinationError:
                raise
            except Exception:
                reject_export_destination(
                    ExportDestinationErrorCode.WRITE_FAILED,
                    "export.write",
                )
            if type(written) is not int or not 1 <= written <= len(chunk) - offset:
                reject_export_destination(
                    ExportDestinationErrorCode.WRITE_FAILED,
                    "export.short_write",
                )
            offset += written
        completed += len(chunk)

    try:
        surplus = source.read(1)
    except Exception:
        reject_export_destination(
            ExportDestinationErrorCode.SOURCE_INVALID,
            "export.source_eof",
        )
    if surplus != b"" or not hmac.compare_digest(digest.hexdigest(), expected_sha256):
        reject_export_destination(
            ExportDestinationErrorCode.SOURCE_MISMATCH,
            "export.source_verify",
        )

    _require_not_cancelled(cancellation)
    checkpoint(ExportDestinationPhase.BEFORE_FLUSH)
    try:
        flush_stage(stage)
    except OSError as error:
        _map_os_error(error, context="export.flush")
    except ExportDestinationError:
        raise
    except Exception:
        reject_export_destination(
            ExportDestinationErrorCode.FLUSH_FAILED,
            "export.flush",
        )

    checkpoint(ExportDestinationPhase.BEFORE_READBACK)
    _verify_stage_readback(
        stage,
        stage_path,
        expected_byte_size=expected_byte_size,
        expected_sha256=expected_sha256,
    )


def _verify_stage_readback(
    stage: WindowsHandle,
    stage_path: Path,
    *,
    expected_byte_size: int,
    expected_sha256: str,
) -> None:
    initial = stage.info
    try:
        current = stage.refresh_info()
        if (
            current.identity != initial.identity
            or current.logical_size != expected_byte_size
            or current.is_directory
            or current.is_reparse_point
            or current.is_sparse
            or current.link_count != 1
            or _normalized(current.final_path) != _normalized(stage_path)
        ):
            reject_export_destination(
                ExportDestinationErrorCode.VERIFY_FAILED,
                "export.stage_metadata",
            )
        require_no_alternate_streams(current.final_path)
        stage.rewind()
        digest = hashlib.sha256()
        completed = 0
        while completed < expected_byte_size:
            count = min(MAX_EXPORT_DESTINATION_CHUNK_BYTES, expected_byte_size - completed)
            digest.update(stage.read_exact(count))
            completed += count
        final = stage.require_eof()
        if (
            final.identity != initial.identity
            or final.logical_size != expected_byte_size
            or not hmac.compare_digest(digest.hexdigest(), expected_sha256)
        ):
            reject_export_destination(
                ExportDestinationErrorCode.VERIFY_FAILED,
                "export.stage_readback",
            )
    except ExportDestinationError:
        raise
    except WorkspaceError:
        reject_export_destination(
            ExportDestinationErrorCode.VERIFY_FAILED,
            "export.stage_readback",
        )


def _require_not_cancelled(cancellation: Callable[[], bool]) -> None:
    try:
        cancelled = cancellation()
    except Exception:
        reject_export_destination(
            ExportDestinationErrorCode.INTERNAL_ERROR,
            "export.cancel_probe",
        )
    if type(cancelled) is not bool:
        reject_export_destination(
            ExportDestinationErrorCode.INTERNAL_ERROR,
            "export.cancel_probe",
        )
    if cancelled:
        reject_export_destination(
            ExportDestinationErrorCode.CANCELLED,
            "export.cancelled",
        )


def _resolve_commit_state(
    stage: WindowsHandle,
    *,
    stage_path: Path,
    destination: Path,
    expected_identity: FileIdentity,
) -> ExportCommitState:
    try:
        current = stage.refresh_info()
        if (
            current.identity != expected_identity
            or current.is_directory
            or current.is_reparse_point
        ):
            return ExportCommitState.UNKNOWN
        if _normalized(current.final_path) == _normalized(destination):
            return ExportCommitState.COMMITTED
        if _normalized(current.final_path) == _normalized(stage_path):
            if os.path.lexists(destination):
                return _different_destination_state(destination, expected_identity)
            return ExportCommitState.NOT_COMMITTED
        return ExportCommitState.UNKNOWN
    except Exception:
        if os.path.lexists(destination):
            return _different_destination_state(destination, expected_identity)
        return ExportCommitState.UNKNOWN


def _different_destination_state(
    destination: Path, stage_identity: FileIdentity
) -> ExportCommitState:
    try:
        with open_handle(destination, share_delete=True) as existing:
            require_expected_handle(existing, destination, directory=False)
            current = existing.refresh_info()
            if current.is_reparse_point:
                return ExportCommitState.UNKNOWN
            if current.identity == stage_identity:
                return ExportCommitState.COMMITTED
            return ExportCommitState.DESTINATION_EXISTS
    except Exception:
        return ExportCommitState.UNKNOWN


def _require_committed_identity(
    stage: WindowsHandle,
    destination: Path,
    *,
    expected_identity: FileIdentity,
    expected_byte_size: int,
) -> None:
    current = stage.refresh_info()
    if (
        current.identity != expected_identity
        or current.logical_size != expected_byte_size
        or current.is_directory
        or current.is_reparse_point
        or current.is_sparse
        or current.link_count != 1
        or _normalized(current.final_path) != _normalized(destination)
    ):
        reject_export_destination(
            ExportDestinationErrorCode.COMMIT_FAILED,
            "export.commit_identity",
        )
    require_no_alternate_streams(current.final_path)


def _verify_committed_reopen(
    destination: Path,
    *,
    expected_identity: FileIdentity,
    expected_byte_size: int,
    expected_sha256: str,
    open_committed: CommittedOpener,
    read_committed: CommittedReader,
    close_committed: CommittedCloser,
) -> bool:
    """Reopen the final name, stream-verify it, and close before reporting success."""

    reopened: WindowsHandle | None = None
    verified = False
    try:
        reopened = open_committed(destination)
        if type(reopened) is not WindowsHandle:
            return False
        require_expected_handle(
            reopened,
            destination,
            directory=False,
            expected_identity=expected_identity,
        )
        current = reopened.refresh_info()
        if (
            current.identity != expected_identity
            or current.logical_size != expected_byte_size
            or current.is_directory
            or current.is_reparse_point
            or current.is_sparse
            or current.link_count != 1
            or _normalized(current.final_path) != _normalized(destination)
        ):
            return False
        require_no_alternate_streams(current.final_path)
        reopened.rewind()
        digest = hashlib.sha256()
        completed = 0
        while completed < expected_byte_size:
            count = min(MAX_EXPORT_DESTINATION_CHUNK_BYTES, expected_byte_size - completed)
            chunk = read_committed(reopened, count)
            if type(chunk) is not bytes or len(chunk) != count:
                return False
            digest.update(chunk)
            completed += count
        final = reopened.require_eof()
        verified = (
            final.identity == expected_identity
            and final.logical_size == expected_byte_size
            and not final.is_directory
            and not final.is_reparse_point
            and not final.is_sparse
            and final.link_count == 1
            and _normalized(final.final_path) == _normalized(destination)
            and hmac.compare_digest(digest.hexdigest(), expected_sha256)
        )
    except BaseException:
        verified = False
    finally:
        if reopened is not None:
            try:
                close_committed(reopened)
            except BaseException:
                verified = False
            if reopened.value != 0:
                verified = False
    return verified


def _open_committed_destination(destination: Path) -> WindowsHandle:
    return open_handle(
        destination,
        read=True,
        share_delete=True,
        share_write=True,
    )


def _read_committed_chunk(handle: WindowsHandle, count: int) -> bytes:
    return handle.read_exact(count)


def _close_committed_destination(handle: WindowsHandle) -> None:
    handle.close()


def _create_exclusive_stage(path: Path) -> WindowsHandle:
    """Create one retained stage whose cleanup disposition is set only after outcome.

    ``FILE_FLAG_DELETE_ON_CLOSE`` is intentionally not used: Windows does not allow
    that flag's delete disposition to be cleared after a successful rename.  Known
    non-commits are deleted through this exact handle; unknown outcomes are retained.
    """

    if _STAGE_NAME_RE.fullmatch(path.name) is None:
        reject_export_destination(
            ExportDestinationErrorCode.STAGE_CREATE_FAILED,
            "export.stage_name",
        )
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    raw = create_file(
        str(path),
        _GENERIC_READ
        | _GENERIC_WRITE
        | _DELETE
        | _READ_CONTROL
        | _FILE_READ_ATTRIBUTES
        | _SYNCHRONIZE,
        _FILE_SHARE_READ,
        None,
        _CREATE_NEW,
        _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_SEQUENTIAL_SCAN,
        None,
    )
    value = int(raw) if raw else 0
    if not value or value == _INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return WindowsHandle(value, _query_opened_info(value))
    except BaseException:
        cleanup_failed = False
        try:
            _set_raw_deletion_disposition(value, True)
        except BaseException:
            cleanup_failed = True
        close = kernel32.CloseHandle
        close.argtypes = (wintypes.HANDLE,)
        close.restype = wintypes.BOOL
        if not close(wintypes.HANDLE(value)):
            cleanup_failed = True
        if cleanup_failed:
            reject_export_destination(
                ExportDestinationErrorCode.STAGE_CLEANUP_FAILED,
                "export.stage_cleanup",
            )
        raise


def _write_stage_once(stage: WindowsHandle, payload: bytes) -> int:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    write_file = kernel32.WriteFile
    write_file.argtypes = (
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    )
    write_file.restype = wintypes.BOOL
    buffer = ctypes.create_string_buffer(payload)
    written = wintypes.DWORD()
    if not write_file(
        wintypes.HANDLE(stage.value),
        buffer,
        len(payload),
        ctypes.byref(written),
        None,
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(written.value)


def _flush_stage(stage: WindowsHandle) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    flush = kernel32.FlushFileBuffers
    flush.argtypes = (wintypes.HANDLE,)
    flush.restype = wintypes.BOOL
    if not flush(wintypes.HANDLE(stage.value)):
        raise ctypes.WinError(ctypes.get_last_error())


def _rename_stage_no_replace(
    stage: WindowsHandle,
    destination_parent: WindowsHandle,
    destination_name: str,
) -> tuple[bool, int]:
    return rename_open_file_no_replace_at(
        stage,
        destination_parent,
        destination_name=destination_name,
    )


class _FileDispositionInfo(ctypes.Structure):
    _fields_ = (("delete_file", wintypes.BOOLEAN),)


def _set_deletion_disposition(stage: WindowsHandle, enabled: bool) -> None:
    _set_raw_deletion_disposition(stage.value, enabled)


def _set_raw_deletion_disposition(handle: int, enabled: bool) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    set_information = kernel32.SetFileInformationByHandle
    set_information.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    set_information.restype = wintypes.BOOL
    disposition = _FileDispositionInfo(wintypes.BOOLEAN(enabled))
    if not set_information(
        wintypes.HANDLE(handle),
        _FILE_DISPOSITION_INFO_CLASS,
        ctypes.byref(disposition),
        ctypes.sizeof(disposition),
    ):
        raise ctypes.WinError(ctypes.get_last_error())


def _finish_stage(
    stage: WindowsHandle,
    *,
    cleanup: bool,
    set_deletion: DeletionSetter,
) -> None:
    cleanup_failed = False
    if cleanup:
        try:
            set_deletion(stage, True)
        except BaseException:
            cleanup_failed = True
    try:
        stage.close()
    except BaseException:
        if cleanup:
            cleanup_failed = True
    if cleanup_failed:
        reject_export_destination(
            ExportDestinationErrorCode.STAGE_CLEANUP_FAILED,
            "export.stage_cleanup",
        )


def _normalized(value: Path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(value)))


def _error_number(error: OSError) -> int | None:
    winerror = getattr(error, "winerror", None)
    if type(winerror) is int:
        return winerror
    return error.errno if type(error.errno) is int else None


def _map_os_error(error: OSError, *, context: str) -> Never:
    number = _error_number(error)
    if number in {_ERROR_DISK_FULL, _ERROR_HANDLE_DISK_FULL}:
        reject_export_destination(ExportDestinationErrorCode.DESTINATION_FULL, context)
    if number in {_ERROR_ACCESS_DENIED, _ERROR_WRITE_PROTECT}:
        reject_export_destination(ExportDestinationErrorCode.DESTINATION_DENIED, context)
    if context == "export.flush":
        reject_export_destination(ExportDestinationErrorCode.FLUSH_FAILED, context)
    if context == "export.write":
        reject_export_destination(ExportDestinationErrorCode.WRITE_FAILED, context)
    if context == "export.stage_create":
        reject_export_destination(ExportDestinationErrorCode.STAGE_CREATE_FAILED, context)
    reject_export_destination(ExportDestinationErrorCode.INTERNAL_ERROR, context)


def _map_workspace_error(error: WorkspaceError) -> Never:
    context = error.context
    if "volume" in context or "permissions" in context:
        reject_export_destination(
            ExportDestinationErrorCode.LOCATION_UNSUPPORTED,
            "export.volume",
        )
    if "reparse" in context or "containment" in context or "identity" in context:
        reject_export_destination(
            ExportDestinationErrorCode.DESTINATION_UNSAFE,
            "export.identity",
        )
    reject_export_destination(
        ExportDestinationErrorCode.PARENT_UNAVAILABLE,
        "export.parent",
    )

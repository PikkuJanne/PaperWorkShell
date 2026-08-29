"""Destination-local staging and per-artifact atomic package publication.

The package boundary deliberately does not claim that Windows can atomically rename
several unrelated files as one set.  Every verified artifact is staged and retained
before publication starts.  Each final rename is no-replace and identity checked; a
failure returns the exact ordered commit inventory and never turns a partial set into
a package-success result.
"""

from __future__ import annotations

import ctypes
import hashlib
import hmac
import os
import re
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO, Final, Never, Protocol, Self
from uuid import UUID, uuid4

from paperworkshell.storage._windows import (
    FileIdentity,
    WindowsHandle,
    open_handle,
    require_expected_handle,
    require_local_acl_volume,
    require_no_alternate_streams,
)
from paperworkshell.storage.errors import WorkspaceError
from paperworkshell.storage.export_destination import (
    MAX_EXPORT_DESTINATION_BYTES,
    MAX_EXPORT_DESTINATION_CHUNK_BYTES,
    ExportCommitState,
    ExportDestinationError,
    ExportDestinationResult,
    WindowsExportDestination,
    _authenticate_parent,
    _close_committed_destination,
    _create_exclusive_stage,
    _existing_destination_state,
    _flush_stage,
    _map_os_error,
    _normalized,
    _open_committed_destination,
    _read_committed_chunk,
    _rename_stage_no_replace,
    _require_committed_identity,
    _resolve_commit_state,
    _set_deletion_disposition,
    _validate_destination,
    _verify_committed_reopen,
)

MAX_PACKAGE_ARTIFACTS: Final = 128
MAX_PACKAGE_STAGE_ATTEMPTS: Final = 32
_CONTEXT = re.compile(r"[a-z][a-z0-9_.]{0,63}\Z")
_TOKEN = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class ExportPackageDestinationErrorCode(StrEnum):
    """Closed, content-free package destination failures."""

    MODEL_INVALID = "EXPORT_PACKAGE_DESTINATION_MODEL_INVALID"
    PLATFORM_UNSUPPORTED = "EXPORT_PACKAGE_DESTINATION_PLATFORM_UNSUPPORTED"
    LOCATION_UNSUPPORTED = "EXPORT_PACKAGE_DESTINATION_LOCATION_UNSUPPORTED"
    CAPACITY_EXCEEDED = "EXPORT_PACKAGE_DESTINATION_CAPACITY_EXCEEDED"
    DESTINATION_DENIED = "EXPORT_PACKAGE_DESTINATION_DENIED"
    DESTINATION_FULL = "EXPORT_PACKAGE_DESTINATION_FULL"
    STAGE_FAILED = "EXPORT_PACKAGE_DESTINATION_STAGE_FAILED"
    STAGE_CHANGED = "EXPORT_PACKAGE_DESTINATION_STAGE_CHANGED"
    CLEANUP_FAILED = "EXPORT_PACKAGE_DESTINATION_CLEANUP_FAILED"
    COMMIT_FAILED = "EXPORT_PACKAGE_DESTINATION_COMMIT_FAILED"
    CANCELLED = "EXPORT_PACKAGE_DESTINATION_CANCELLED"
    TIMED_OUT = "EXPORT_PACKAGE_DESTINATION_TIMED_OUT"
    INTERNAL_ERROR = "EXPORT_PACKAGE_DESTINATION_INTERNAL_ERROR"


_SAFE_MESSAGES: Final[dict[ExportPackageDestinationErrorCode, str]] = {
    ExportPackageDestinationErrorCode.MODEL_INVALID: (
        "PaperWorkShell received invalid package-destination data."
    ),
    ExportPackageDestinationErrorCode.PLATFORM_UNSUPPORTED: (
        "Verified package publication requires the supported Windows storage boundary."
    ),
    ExportPackageDestinationErrorCode.LOCATION_UNSUPPORTED: (
        "Choose a supported fixed local destination folder."
    ),
    ExportPackageDestinationErrorCode.CAPACITY_EXCEEDED: (
        "The package exceeds the configured temporary-storage limit."
    ),
    ExportPackageDestinationErrorCode.DESTINATION_DENIED: (
        "PaperWorkShell does not have permission to write the selected package destination."
    ),
    ExportPackageDestinationErrorCode.DESTINATION_FULL: (
        "The selected package destination does not have enough available storage."
    ),
    ExportPackageDestinationErrorCode.STAGE_FAILED: (
        "PaperWorkShell could not stage the verified package output safely."
    ),
    ExportPackageDestinationErrorCode.STAGE_CHANGED: (
        "A staged package output changed and the package was not reported as successful."
    ),
    ExportPackageDestinationErrorCode.CLEANUP_FAILED: (
        "PaperWorkShell could not confirm cleanup of a private package stage."
    ),
    ExportPackageDestinationErrorCode.COMMIT_FAILED: (
        "PaperWorkShell could not complete verified package publication."
    ),
    ExportPackageDestinationErrorCode.CANCELLED: (
        "Package publication was cancelled before the commit phase."
    ),
    ExportPackageDestinationErrorCode.TIMED_OUT: (
        "Package publication exceeded its deadline before the commit phase."
    ),
    ExportPackageDestinationErrorCode.INTERNAL_ERROR: (
        "PaperWorkShell could not complete package publication safely."
    ),
}


@dataclass(slots=True, repr=False)
class ExportPackageDestinationError(ValueError):
    """Stable failure that never includes a path, filename, or digest."""

    code: ExportPackageDestinationErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not ExportPackageDestinationErrorCode:
            self.code = ExportPackageDestinationErrorCode.INTERNAL_ERROR
        if type(self.context) is not str or _CONTEXT.fullmatch(self.context) is None:
            self.context = "package_destination.operation"
        self.safe_message = _SAFE_MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.safe_message)

    def __repr__(self) -> str:
        return f"ExportPackageDestinationError(code={self.code.value!r}, context={self.context!r})"

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject_export_package_destination(
    code: ExportPackageDestinationErrorCode,
    context: str,
) -> Never:
    """Raise one sanitized package-destination failure without exception chaining."""

    error = ExportPackageDestinationError(code, context)
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None


class ExportPackageDestinationPhase(StrEnum):
    """Stable failure-injection checkpoints for package publication tests."""

    BEFORE_STAGE = "before_stage"
    AFTER_STAGE = "after_stage"
    BEFORE_COMMIT = "before_commit"
    BEFORE_RENAME = "before_rename"
    AFTER_RENAME = "after_rename"
    BEFORE_FINAL_REOPEN = "before_final_reopen"
    BEFORE_CLEANUP = "before_cleanup"


class BoundedPackageSource(Protocol):
    """Single-pass bounded bytes accepted from a verified producer."""

    def read(self, size: int, /) -> bytes: ...


GeneratedPackageWriter = Callable[[BinaryIO], tuple[int, str]]


PackageCheckpoint = Callable[[ExportPackageDestinationPhase, int], None]
PackageRename = Callable[[WindowsHandle, WindowsHandle, str], tuple[bool, int]]
PackageFinalVerifier = Callable[[Path, FileIdentity, int, str], bool]


def _no_checkpoint(_phase: ExportPackageDestinationPhase, _position: int) -> None:
    return


@dataclass(frozen=True, slots=True)
class ExportPackageDestinationDependencies:
    """Injectable seams for deterministic partial-commit and recovery tests."""

    id_factory: Callable[[], UUID] = uuid4
    checkpoint: PackageCheckpoint = _no_checkpoint
    rename_stage: PackageRename | None = field(default=None, repr=False)
    verify_final: PackageFinalVerifier | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if (
            not callable(self.id_factory)
            or not callable(self.checkpoint)
            or (self.rename_stage is not None and not callable(self.rename_stage))
            or (self.verify_final is not None and not callable(self.verify_final))
        ):
            reject_export_package_destination(
                ExportPackageDestinationErrorCode.MODEL_INVALID,
                "package_destination.dependencies",
            )


@dataclass(frozen=True, slots=True, repr=False)
class PackageArtifactCommitResult:
    """Path-free terminal state for one ordered top-level artifact."""

    output_key: str
    position: int
    state: ExportCommitState
    byte_size: int | None = None
    sha256: str | None = field(default=None, repr=False)
    destination_reopened: bool = False

    def __post_init__(self) -> None:
        has_verified_output = self.state is ExportCommitState.COMMITTED
        if (
            type(self.output_key) is not str
            or _TOKEN.fullmatch(self.output_key) is None
            or type(self.position) is not int
            or not 1 <= self.position <= MAX_PACKAGE_ARTIFACTS
            or type(self.state) is not ExportCommitState
            or type(self.destination_reopened) is not bool
            or (
                has_verified_output
                and (
                    type(self.byte_size) is not int
                    or not 1 <= self.byte_size <= MAX_EXPORT_DESTINATION_BYTES
                    or type(self.sha256) is not str
                    or _SHA256.fullmatch(self.sha256) is None
                    or not self.destination_reopened
                )
            )
            or (
                not has_verified_output
                and (
                    self.byte_size is not None
                    or self.sha256 is not None
                    or self.destination_reopened
                )
            )
        ):
            reject_export_package_destination(
                ExportPackageDestinationErrorCode.MODEL_INVALID,
                "package_destination.artifact_result",
            )

    def __repr__(self) -> str:
        return (
            "PackageArtifactCommitResult("
            f"position={self.position!r}, state={self.state.value!r}, "
            f"byte_size={self.byte_size!r}, destination_reopened={self.destination_reopened!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class ExportPackageDestinationResult:
    """Exact ordered public artifact inventory after one commit attempt."""

    artifacts: tuple[PackageArtifactCommitResult, ...]

    def __post_init__(self) -> None:
        if (
            type(self.artifacts) is not tuple
            or not 1 <= len(self.artifacts) <= MAX_PACKAGE_ARTIFACTS
            or any(type(item) is not PackageArtifactCommitResult for item in self.artifacts)
            or tuple(item.position for item in self.artifacts)
            != tuple(range(1, len(self.artifacts) + 1))
            or len({item.output_key for item in self.artifacts}) != len(self.artifacts)
        ):
            reject_export_package_destination(
                ExportPackageDestinationErrorCode.MODEL_INVALID,
                "package_destination.result",
            )

    @property
    def committed(self) -> bool:
        return all(item.state is ExportCommitState.COMMITTED for item in self.artifacts)

    @property
    def partial(self) -> bool:
        committed = sum(item.state is ExportCommitState.COMMITTED for item in self.artifacts)
        return 0 < committed < len(self.artifacts)

    @property
    def uncertain(self) -> bool:
        return any(item.state is ExportCommitState.UNKNOWN for item in self.artifacts)

    def __repr__(self) -> str:
        return (
            "ExportPackageDestinationResult("
            f"artifact_count={len(self.artifacts)}, committed={self.committed!r}, "
            f"partial={self.partial!r}, uncertain={self.uncertain!r})"
        )


@dataclass(slots=True, repr=False)
class _StagedArtifact:
    output_key: str
    position: int
    final_name: str | None = field(repr=False)
    stage_path: Path = field(repr=False)
    handle: WindowsHandle = field(repr=False)
    byte_size: int
    sha256: str = field(repr=False)
    cleanup: bool = True

    def __repr__(self) -> str:
        return (
            "_StagedArtifact("
            f"position={self.position!r}, byte_size={self.byte_size!r}, "
            f"is_public={self.final_name is not None!r})"
        )


class WindowsExportPackageTransaction:
    """Retain all exact stages, then publish top-level artifacts without replacement."""

    __slots__ = (
        "_artifacts",
        "_artifact_destination",
        "_cancelled",
        "_closed",
        "_committed",
        "_deadline",
        "_dependencies",
        "_maximum_staged_bytes",
        "_parent",
        "_parent_path",
        "_staged_bytes",
    )

    def __init__(
        self,
        parent: Path,
        artifact_destination: WindowsExportDestination,
        *,
        maximum_staged_bytes: int,
        cancellation: Callable[[], bool],
        deadline: Callable[[], bool],
        dependencies: ExportPackageDestinationDependencies | None = None,
    ) -> None:
        if (
            not isinstance(parent, Path)
            or type(artifact_destination) is not WindowsExportDestination
            or type(maximum_staged_bytes) is not int
            or not 1 <= maximum_staged_bytes <= MAX_EXPORT_DESTINATION_BYTES
            or not callable(cancellation)
            or not callable(deadline)
            or (
                dependencies is not None
                and type(dependencies) is not ExportPackageDestinationDependencies
            )
        ):
            reject_export_package_destination(
                ExportPackageDestinationErrorCode.MODEL_INVALID,
                "package_destination.transaction",
            )
        if os.name != "nt":
            reject_export_package_destination(
                ExportPackageDestinationErrorCode.PLATFORM_UNSUPPORTED,
                "package_destination.platform",
            )
        selected = _validate_destination(parent / "pws-package-authority.tmp").parent
        try:
            require_local_acl_volume(selected)
            retained_parent = open_handle(selected, traverse=True)
            _authenticate_parent(retained_parent, selected)
        except ExportDestinationError:
            raise
        except WorkspaceError:
            reject_export_package_destination(
                ExportPackageDestinationErrorCode.LOCATION_UNSUPPORTED,
                "package_destination.parent",
            )
        except PermissionError:
            reject_export_package_destination(
                ExportPackageDestinationErrorCode.DESTINATION_DENIED,
                "package_destination.parent",
            )
        except BaseException:
            reject_export_package_destination(
                ExportPackageDestinationErrorCode.INTERNAL_ERROR,
                "package_destination.parent",
            )
        self._parent_path = selected
        self._parent = retained_parent
        self._artifact_destination = artifact_destination
        self._maximum_staged_bytes = maximum_staged_bytes
        self._cancelled = cancellation
        self._deadline = deadline
        self._dependencies = dependencies or ExportPackageDestinationDependencies()
        self._artifacts: list[_StagedArtifact] = []
        self._staged_bytes = 0
        self._committed = False
        self._closed = False

    def __enter__(self) -> Self:
        if self._closed:
            reject_export_package_destination(
                ExportPackageDestinationErrorCode.MODEL_INVALID,
                "package_destination.closed",
            )
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @property
    def staged_byte_size(self) -> int:
        return self._staged_bytes

    @property
    def remaining_staged_bytes(self) -> int:
        return self._maximum_staged_bytes - self._staged_bytes

    @property
    def staged_keys(self) -> tuple[str, ...]:
        return tuple(item.output_key for item in self._artifacts)

    def stage(
        self,
        output_key: str,
        position: int,
        final_name: str | None,
        source: BoundedPackageSource,
        *,
        expected_byte_size: int,
        expected_sha256: str,
    ) -> None:
        """Copy one verified stream to a retained random destination-local stage."""

        self._require_open()
        if (
            type(output_key) is not str
            or _TOKEN.fullmatch(output_key) is None
            or output_key in self.staged_keys
            or type(position) is not int
            or position != len(self._artifacts) + 1
            or (final_name is not None and type(final_name) is not str)
            or type(expected_byte_size) is not int
            or not 1 <= expected_byte_size <= MAX_EXPORT_DESTINATION_BYTES
            or type(expected_sha256) is not str
            or _SHA256.fullmatch(expected_sha256) is None
            or not callable(getattr(source, "read", None))
        ):
            reject_export_package_destination(
                ExportPackageDestinationErrorCode.MODEL_INVALID,
                "package_destination.stage_request",
            )
        if final_name is not None:
            _validate_destination(self._parent_path / final_name)
            prior_names = tuple(
                item.final_name.casefold()
                for item in self._artifacts
                if item.final_name is not None
            )
            if final_name.casefold() in prior_names:
                reject_export_package_destination(
                    ExportPackageDestinationErrorCode.MODEL_INVALID,
                    "package_destination.final_names",
                )
        if expected_byte_size > self.remaining_staged_bytes:
            reject_export_package_destination(
                ExportPackageDestinationErrorCode.CAPACITY_EXCEEDED,
                "package_destination.stage_budget",
            )
        self._require_active()
        self._checkpoint(ExportPackageDestinationPhase.BEFORE_STAGE, position)
        stage = self._stage_exact_source(
            source,
            expected_byte_size=expected_byte_size,
            expected_sha256=expected_sha256,
        )
        self._artifacts.append(
            _StagedArtifact(
                output_key,
                position,
                final_name,
                stage[0],
                stage[1],
                expected_byte_size,
                expected_sha256,
            )
        )
        self._staged_bytes += expected_byte_size
        self._checkpoint(ExportPackageDestinationPhase.AFTER_STAGE, position)

    def stage_generated(
        self,
        output_key: str,
        position: int,
        final_name: str,
        writer: GeneratedPackageWriter,
    ) -> None:
        """Generate one verified artifact directly into a retained local stage.

        The trusted writer must return the exact byte size and SHA-256 it verified.
        Direct generation avoids a second archive-sized temporary copy while PDF
        member stages are retained.
        """

        self._require_open()
        if (
            type(output_key) is not str
            or _TOKEN.fullmatch(output_key) is None
            or output_key in self.staged_keys
            or type(position) is not int
            or position != len(self._artifacts) + 1
            or type(final_name) is not str
            or not callable(writer)
        ):
            reject_export_package_destination(
                ExportPackageDestinationErrorCode.MODEL_INVALID,
                "package_destination.generate_request",
            )
        _validate_destination(self._parent_path / final_name)
        if final_name.casefold() in {
            str(item.final_name).casefold()
            for item in self._artifacts
            if item.final_name is not None
        }:
            reject_export_package_destination(
                ExportPackageDestinationErrorCode.MODEL_INVALID,
                "package_destination.final_names",
            )
        self._require_active()
        self._checkpoint(ExportPackageDestinationPhase.BEFORE_STAGE, position)

        stage_path: Path | None = None
        stage_handle: WindowsHandle | None = None
        stream: BinaryIO | None = None
        completed = False
        try:
            stage_path, stage_handle = self._create_empty_stage()
            try:
                stream = _duplicate_binary_stream(stage_handle)
            except BaseException:
                reject_export_package_destination(
                    ExportPackageDestinationErrorCode.STAGE_FAILED,
                    "package_destination.generate_open",
                )
            result = writer(stream)
            if (
                type(result) is not tuple
                or len(result) != 2
                or type(result[0]) is not int
                or not 1 <= result[0] <= MAX_EXPORT_DESTINATION_BYTES
                or type(result[1]) is not str
                or _SHA256.fullmatch(result[1]) is None
            ):
                reject_export_package_destination(
                    ExportPackageDestinationErrorCode.STAGE_FAILED,
                    "package_destination.generate_result",
                )
            expected_byte_size, expected_sha256 = result
            if expected_byte_size > self.remaining_staged_bytes:
                reject_export_package_destination(
                    ExportPackageDestinationErrorCode.CAPACITY_EXCEEDED,
                    "package_destination.generate_budget",
                )
            try:
                stream.flush()
                stream.close()
                stream = None
                _flush_stage(stage_handle)
            except OSError as error:
                _map_generated_os_error(error)
            except BaseException:
                reject_export_package_destination(
                    ExportPackageDestinationErrorCode.STAGE_FAILED,
                    "package_destination.generate_flush",
                )
            try:
                _verify_retained_stage(
                    stage_handle,
                    stage_path,
                    self._parent,
                    expected_byte_size=expected_byte_size,
                    expected_sha256=expected_sha256,
                    require_active=self._require_active,
                )
            except ExportPackageDestinationError:
                raise
            except BaseException:
                reject_export_package_destination(
                    ExportPackageDestinationErrorCode.STAGE_CHANGED,
                    "package_destination.generate_verify",
                )
            self._artifacts.append(
                _StagedArtifact(
                    output_key,
                    position,
                    final_name,
                    stage_path,
                    stage_handle,
                    expected_byte_size,
                    expected_sha256,
                )
            )
            self._staged_bytes += expected_byte_size
            completed = True
        finally:
            if stream is not None:
                try:
                    stream.close()
                except BaseException:
                    pass
            if not completed and stage_handle is not None:
                cleanup_failed = False
                try:
                    _delete_retained_stage(stage_handle)
                except BaseException:
                    cleanup_failed = True
                    if stage_handle.value:
                        try:
                            stage_handle.close()
                        except BaseException:
                            cleanup_failed = True
                if cleanup_failed:
                    reject_export_package_destination(
                        ExportPackageDestinationErrorCode.CLEANUP_FAILED,
                        "package_destination.generate_cleanup",
                    )
        self._checkpoint(ExportPackageDestinationPhase.AFTER_STAGE, position)

    @contextmanager
    def open_staged_readers(
        self,
        output_keys: Sequence[str],
    ) -> Iterator[tuple[BinaryIO, ...]]:
        """Open locked stage paths read-only for trusted streaming package construction."""

        self._require_open()
        if (
            not isinstance(output_keys, Sequence)
            or isinstance(output_keys, str | bytes)
            or not output_keys
            or any(type(value) is not str for value in output_keys)
            or len(set(output_keys)) != len(output_keys)
        ):
            reject_export_package_destination(
                ExportPackageDestinationErrorCode.MODEL_INVALID,
                "package_destination.readers",
            )
        by_key = {item.output_key: item for item in self._artifacts}
        if any(value not in by_key for value in output_keys):
            reject_export_package_destination(
                ExportPackageDestinationErrorCode.MODEL_INVALID,
                "package_destination.reader_key",
            )
        readers: list[BinaryIO] = []
        try:
            for key in output_keys:
                item = by_key[key]
                item.handle.rewind()
                opened_reader = _duplicate_binary_reader(item.handle)
                readers.append(opened_reader)
        except ExportPackageDestinationError:
            raise
        except BaseException:
            reject_export_package_destination(
                ExportPackageDestinationErrorCode.STAGE_CHANGED,
                "package_destination.reader_open",
            )
        try:
            yield tuple(readers)
        finally:
            failed = False
            for selected_reader in reversed(readers):
                try:
                    selected_reader.close()
                except BaseException:
                    failed = True
            if failed:
                reject_export_package_destination(
                    ExportPackageDestinationErrorCode.STAGE_CHANGED,
                    "package_destination.reader_close",
                )

    def discard_internal(self) -> None:
        """Delete every non-public exact stage before a public package commit."""

        self._require_open()
        kept: list[_StagedArtifact] = []
        for item in self._artifacts:
            if item.final_name is not None:
                kept.append(item)
                continue
            self._cleanup_artifact(item, strict=True)
            self._staged_bytes -= item.byte_size
        self._artifacts = kept
        for index, item in enumerate(self._artifacts, start=1):
            item.position = index

    def commit(self) -> ExportPackageDestinationResult:
        """Publish all public stages per artifact and return an exact partial-set inventory."""

        self._require_open()
        public = tuple(item for item in self._artifacts if item.final_name is not None)
        if not public or len(public) != len(self._artifacts):
            reject_export_package_destination(
                ExportPackageDestinationErrorCode.MODEL_INVALID,
                "package_destination.public_inventory",
            )
        self._require_active()
        self._checkpoint(ExportPackageDestinationPhase.BEFORE_COMMIT, 0)
        try:
            _authenticate_parent(self._parent, self._parent_path)
            existing = tuple(
                _existing_destination_state(self._parent_path / str(item.final_name))
                for item in public
            )
        except ExportDestinationError:
            raise
        except BaseException:
            reject_export_package_destination(
                ExportPackageDestinationErrorCode.COMMIT_FAILED,
                "package_destination.precommit",
            )
        if any(value is not None for value in existing):
            existing_results = tuple(
                PackageArtifactCommitResult(
                    item.output_key,
                    index,
                    (
                        ExportCommitState.DESTINATION_EXISTS
                        if observed is not None
                        else ExportCommitState.NOT_COMMITTED
                    ),
                )
                for index, (item, observed) in enumerate(
                    zip(public, existing, strict=True), start=1
                )
            )
            self._cleanup_uncommitted(public)
            self._committed = True
            return ExportPackageDestinationResult(existing_results)

        results: list[PackageArtifactCommitResult] = []
        stopped = False
        for index, item in enumerate(public, start=1):
            if stopped:
                results.append(
                    PackageArtifactCommitResult(
                        item.output_key,
                        index,
                        ExportCommitState.NOT_COMMITTED,
                    )
                )
                continue
            result = self._commit_one(item, public_position=index)
            results.append(result)
            stopped = result.state is not ExportCommitState.COMMITTED
        cleanup_protocol_failed = False
        cleanup_unconfirmed = False
        if stopped:
            for item, result in zip(public, results, strict=True):
                if result.state in {
                    ExportCommitState.NOT_COMMITTED,
                    ExportCommitState.DESTINATION_EXISTS,
                }:
                    try:
                        self._cleanup_artifact(item, strict=True)
                    except ExportPackageDestinationError:
                        try:
                            # A bounded immediate retry recovers transient cleanup
                            # failures while the exact retained handle is still owned.
                            self._cleanup_artifact(item, strict=True)
                        except ExportPackageDestinationError:
                            cleanup_protocol_failed = True
                            try:
                                # Do not let an injected/protocol checkpoint prevent
                                # deletion of the already-authenticated sensitive stage.
                                self._cleanup_artifact(
                                    item,
                                    strict=True,
                                    run_checkpoint=False,
                                )
                            except ExportPackageDestinationError:
                                cleanup_unconfirmed = True
        self._committed = not cleanup_unconfirmed
        self._close_handles_preserving_unknown(public, tuple(results))
        destination_result = ExportPackageDestinationResult(tuple(results))
        if cleanup_protocol_failed or cleanup_unconfirmed:
            reject_export_package_destination(
                ExportPackageDestinationErrorCode.CLEANUP_FAILED,
                "package_destination.partial_cleanup",
            )
        return destination_result

    def close(self) -> None:
        if self._closed:
            return
        cleanup_error = False
        if not self._committed:
            for item in self._artifacts:
                try:
                    self._cleanup_artifact(item, strict=True)
                except BaseException:
                    cleanup_error = True
                    try:
                        self._cleanup_artifact(
                            item,
                            strict=True,
                            run_checkpoint=False,
                        )
                    except BaseException:
                        cleanup_error = True
        for item in self._artifacts:
            if item.handle.value:
                try:
                    item.handle.close()
                except BaseException:
                    cleanup_error = True
        try:
            self._parent.close()
        except BaseException:
            cleanup_error = True
        self._closed = True
        if cleanup_error:
            reject_export_package_destination(
                ExportPackageDestinationErrorCode.CLEANUP_FAILED,
                "package_destination.close",
            )

    def _stage_exact_source(
        self,
        source: BoundedPackageSource,
        *,
        expected_byte_size: int,
        expected_sha256: str,
    ) -> tuple[Path, WindowsHandle]:
        for _ in range(MAX_PACKAGE_STAGE_ATTEMPTS):
            try:
                identifier = self._dependencies.id_factory()
            except BaseException:
                reject_export_package_destination(
                    ExportPackageDestinationErrorCode.STAGE_FAILED,
                    "package_destination.stage_id",
                )
            if type(identifier) is not UUID or identifier.version != 4:
                reject_export_package_destination(
                    ExportPackageDestinationErrorCode.STAGE_FAILED,
                    "package_destination.stage_id",
                )
            path = self._parent_path / f".{identifier}.pws-package-stage.tmp"
            try:
                result = self._artifact_destination.commit(
                    path,
                    source,
                    expected_byte_size=expected_byte_size,
                    expected_sha256=expected_sha256,
                    cancellation=self._cancelled,
                    deadline=self._deadline,
                )
            except ExportDestinationError as error:
                _map_single_destination_error(error)
            except BaseException:
                reject_export_package_destination(
                    ExportPackageDestinationErrorCode.STAGE_FAILED,
                    "package_destination.stage",
                )
            if type(result) is not ExportDestinationResult:
                reject_export_package_destination(
                    ExportPackageDestinationErrorCode.STAGE_FAILED,
                    "package_destination.stage_result",
                )
            if result.state is ExportCommitState.DESTINATION_EXISTS:
                continue
            if not result.committed:
                reject_export_package_destination(
                    ExportPackageDestinationErrorCode.STAGE_FAILED,
                    "package_destination.stage_result",
                )
            handle: WindowsHandle | None = None
            try:
                handle = open_handle(path, delete=True, read=True)
                _verify_retained_stage(
                    handle,
                    path,
                    self._parent,
                    expected_byte_size=expected_byte_size,
                    expected_sha256=expected_sha256,
                    require_active=self._require_active,
                )
            except ExportPackageDestinationError:
                self._cleanup_unowned_stage(path, handle)
                raise
            except BaseException:
                self._cleanup_unowned_stage(path, handle)
                reject_export_package_destination(
                    ExportPackageDestinationErrorCode.STAGE_CHANGED,
                    "package_destination.stage_reopen",
                )
            assert handle is not None
            return path, handle
        reject_export_package_destination(
            ExportPackageDestinationErrorCode.STAGE_FAILED,
            "package_destination.stage_collision",
        )

    def _create_empty_stage(self) -> tuple[Path, WindowsHandle]:
        for _ in range(MAX_PACKAGE_STAGE_ATTEMPTS):
            try:
                identifier = self._dependencies.id_factory()
            except BaseException:
                reject_export_package_destination(
                    ExportPackageDestinationErrorCode.STAGE_FAILED,
                    "package_destination.stage_id",
                )
            if type(identifier) is not UUID or identifier.version != 4:
                reject_export_package_destination(
                    ExportPackageDestinationErrorCode.STAGE_FAILED,
                    "package_destination.stage_id",
                )
            path = self._parent_path / f".{identifier}.pws-export.tmp"
            try:
                return path, _create_exclusive_stage(path)
            except FileExistsError:
                continue
            except OSError as error:
                try:
                    _map_os_error(error, context="package_destination.stage_create")
                except ExportDestinationError as mapped:
                    _map_single_destination_error(mapped)
            except BaseException:
                reject_export_package_destination(
                    ExportPackageDestinationErrorCode.STAGE_FAILED,
                    "package_destination.stage_create",
                )
        reject_export_package_destination(
            ExportPackageDestinationErrorCode.STAGE_FAILED,
            "package_destination.stage_collision",
        )

    def _commit_one(
        self,
        item: _StagedArtifact,
        *,
        public_position: int,
    ) -> PackageArtifactCommitResult:
        destination = self._parent_path / str(item.final_name)
        identity = item.handle.info.identity
        self._checkpoint(ExportPackageDestinationPhase.BEFORE_RENAME, public_position)
        try:
            rename = self._dependencies.rename_stage or _rename_stage_no_replace
            value = rename(item.handle, self._parent, str(item.final_name))
            renamed = (
                type(value) is tuple
                and len(value) == 2
                and type(value[0]) is bool
                and type(value[1]) is int
                and value[0]
            )
            if not (
                type(value) is tuple
                and len(value) == 2
                and type(value[0]) is bool
                and type(value[1]) is int
            ):
                renamed = False
        except BaseException:
            renamed = False
        try:
            self._checkpoint(ExportPackageDestinationPhase.AFTER_RENAME, public_position)
        except BaseException:
            pass
        try:
            state = _resolve_commit_state(
                item.handle,
                stage_path=item.stage_path,
                destination=destination,
                expected_identity=identity,
            )
        except BaseException:
            state = ExportCommitState.UNKNOWN
        if renamed and state is not ExportCommitState.COMMITTED:
            state = ExportCommitState.UNKNOWN
        if state is not ExportCommitState.COMMITTED:
            if state is ExportCommitState.UNKNOWN:
                item.cleanup = False
            else:
                try:
                    if _existing_destination_state(destination) is not None:
                        state = ExportCommitState.DESTINATION_EXISTS
                except BaseException:
                    state = ExportCommitState.UNKNOWN
            return PackageArtifactCommitResult(item.output_key, public_position, state)
        item.cleanup = False
        try:
            _require_committed_identity(
                item.handle,
                destination,
                expected_identity=identity,
                expected_byte_size=item.byte_size,
            )
            self._checkpoint(
                ExportPackageDestinationPhase.BEFORE_FINAL_REOPEN,
                public_position,
            )
            verifier = self._dependencies.verify_final
            reopened = (
                verifier(destination, identity, item.byte_size, item.sha256)
                if verifier is not None
                else _verify_committed_reopen(
                    destination,
                    expected_identity=identity,
                    expected_byte_size=item.byte_size,
                    expected_sha256=item.sha256,
                    open_committed=_open_committed_destination,
                    read_committed=_read_committed_chunk,
                    close_committed=_close_committed_destination,
                )
            )
        except BaseException:
            reopened = False
        if reopened is not True:
            return PackageArtifactCommitResult(
                item.output_key,
                public_position,
                ExportCommitState.UNKNOWN,
            )
        return PackageArtifactCommitResult(
            item.output_key,
            public_position,
            ExportCommitState.COMMITTED,
            item.byte_size,
            item.sha256,
            True,
        )

    def _cleanup_uncommitted(self, artifacts: Sequence[_StagedArtifact]) -> None:
        failed = False
        for item in artifacts:
            try:
                self._cleanup_artifact(item, strict=True)
            except BaseException:
                failed = True
        if failed:
            reject_export_package_destination(
                ExportPackageDestinationErrorCode.CLEANUP_FAILED,
                "package_destination.precommit_cleanup",
            )

    def _cleanup_artifact(
        self,
        item: _StagedArtifact,
        *,
        strict: bool,
        run_checkpoint: bool = True,
    ) -> None:
        if not item.cleanup:
            return
        if not item.handle.value:
            if strict:
                reject_export_package_destination(
                    ExportPackageDestinationErrorCode.CLEANUP_FAILED,
                    "package_destination.cleanup_authority",
                )
            return
        try:
            if run_checkpoint:
                self._checkpoint(
                    ExportPackageDestinationPhase.BEFORE_CLEANUP,
                    item.position,
                )
            _delete_retained_stage(item.handle)
            item.cleanup = False
        except BaseException:
            if strict:
                reject_export_package_destination(
                    ExportPackageDestinationErrorCode.CLEANUP_FAILED,
                    "package_destination.cleanup",
                )

    def _cleanup_unowned_stage(
        self,
        path: Path,
        handle: WindowsHandle | None,
    ) -> None:
        """Delete a committed private stage that failed before list ownership."""

        selected = handle
        identity_failed = False
        try:
            if selected is None:
                selected = open_handle(path, delete=True, read=True)
            try:
                _require_private_stage_for_cleanup(selected, path, self._parent)
            except BaseException:
                # The exact delete-capable handle is still safer than a path-based
                # delete. Remove that stage name best-effort, then report that the
                # cleanup identity could not be fully confirmed.
                identity_failed = True
            _delete_retained_stage(selected)
            if identity_failed:
                reject_export_package_destination(
                    ExportPackageDestinationErrorCode.CLEANUP_FAILED,
                    "package_destination.stage_reopen_cleanup_identity",
                )
        except BaseException:
            if selected is not None and selected.value:
                try:
                    selected.close()
                except BaseException:
                    pass
            reject_export_package_destination(
                ExportPackageDestinationErrorCode.CLEANUP_FAILED,
                "package_destination.stage_reopen_cleanup",
            )

    def _close_handles_preserving_unknown(
        self,
        artifacts: Sequence[_StagedArtifact],
        results: Sequence[PackageArtifactCommitResult],
    ) -> None:
        for item, result in zip(artifacts, results, strict=True):
            if not item.handle.value:
                continue
            if result.state in {
                ExportCommitState.COMMITTED,
                ExportCommitState.UNKNOWN,
            }:
                try:
                    item.handle.close()
                except BaseException:
                    continue

    def _checkpoint(self, phase: ExportPackageDestinationPhase, position: int) -> None:
        try:
            self._dependencies.checkpoint(phase, position)
        except ExportPackageDestinationError:
            raise
        except PermissionError:
            reject_export_package_destination(
                ExportPackageDestinationErrorCode.DESTINATION_DENIED,
                "package_destination.checkpoint",
            )
        except OSError:
            reject_export_package_destination(
                ExportPackageDestinationErrorCode.DESTINATION_FULL,
                "package_destination.checkpoint",
            )
        except BaseException:
            reject_export_package_destination(
                ExportPackageDestinationErrorCode.COMMIT_FAILED,
                "package_destination.checkpoint",
            )

    def _require_active(self) -> None:
        try:
            cancelled = self._cancelled()
            expired = self._deadline()
        except BaseException:
            reject_export_package_destination(
                ExportPackageDestinationErrorCode.INTERNAL_ERROR,
                "package_destination.probe",
            )
        if type(cancelled) is not bool or type(expired) is not bool:
            reject_export_package_destination(
                ExportPackageDestinationErrorCode.INTERNAL_ERROR,
                "package_destination.probe",
            )
        if cancelled:
            reject_export_package_destination(
                ExportPackageDestinationErrorCode.CANCELLED,
                "package_destination.cancelled",
            )
        if expired:
            reject_export_package_destination(
                ExportPackageDestinationErrorCode.TIMED_OUT,
                "package_destination.deadline",
            )

    def _require_open(self) -> None:
        if self._closed or self._committed:
            reject_export_package_destination(
                ExportPackageDestinationErrorCode.MODEL_INVALID,
                "package_destination.closed",
            )

    def __repr__(self) -> str:
        return (
            "WindowsExportPackageTransaction("
            f"staged_count={len(self._artifacts)}, staged_byte_size={self._staged_bytes}, "
            f"closed={self._closed!r})"
        )


def _verify_retained_stage(
    handle: WindowsHandle,
    path: Path,
    parent: WindowsHandle,
    *,
    expected_byte_size: int,
    expected_sha256: str,
    require_active: Callable[[], None],
) -> None:
    require_expected_handle(handle, path, directory=False)
    initial = handle.info
    current = handle.refresh_info()
    if (
        current.identity != initial.identity
        or current.logical_size != expected_byte_size
        or current.is_directory
        or current.is_reparse_point
        or current.is_sparse
        or current.link_count != 1
        or _normalized(current.final_path.parent) != _normalized(parent.info.final_path)
    ):
        reject_export_package_destination(
            ExportPackageDestinationErrorCode.STAGE_CHANGED,
            "package_destination.stage_identity",
        )
    require_no_alternate_streams(current.final_path)
    handle.rewind()
    digest = hashlib.sha256()
    completed = 0
    while completed < expected_byte_size:
        require_active()
        count = min(MAX_EXPORT_DESTINATION_CHUNK_BYTES, expected_byte_size - completed)
        digest.update(handle.read_exact(count))
        completed += count
    require_active()
    final = handle.require_eof()
    if (
        final.identity != initial.identity
        or final.logical_size != expected_byte_size
        or not hmac.compare_digest(digest.hexdigest(), expected_sha256)
    ):
        reject_export_package_destination(
            ExportPackageDestinationErrorCode.STAGE_CHANGED,
            "package_destination.stage_digest",
        )


def _require_private_stage_for_cleanup(
    handle: WindowsHandle,
    path: Path,
    parent: WindowsHandle,
) -> None:
    """Authenticate a reopened private stage before deleting its exact handle."""

    require_expected_handle(handle, path, directory=False)
    current = handle.refresh_info()
    if (
        current.is_directory
        or current.is_reparse_point
        or current.is_sparse
        or current.link_count != 1
        or _normalized(current.final_path.parent) != _normalized(parent.info.final_path)
    ):
        reject_export_package_destination(
            ExportPackageDestinationErrorCode.CLEANUP_FAILED,
            "package_destination.stage_cleanup_identity",
        )
    require_no_alternate_streams(current.final_path)


def _delete_retained_stage(handle: WindowsHandle) -> None:
    """Mark and close one exact stage, retaining authority for one setter retry."""

    marked = False
    for _ in range(2):
        try:
            _set_deletion_disposition(handle, True)
        except BaseException:
            continue
        marked = True
        break
    if not marked:
        reject_export_package_destination(
            ExportPackageDestinationErrorCode.CLEANUP_FAILED,
            "package_destination.stage_cleanup_mark",
        )
    try:
        handle.close()
    except BaseException:
        reject_export_package_destination(
            ExportPackageDestinationErrorCode.CLEANUP_FAILED,
            "package_destination.stage_cleanup_close",
        )


def _duplicate_binary_reader(handle: WindowsHandle) -> BinaryIO:
    """Return a Python reader over a duplicate of one already-authenticated handle."""

    import msvcrt

    duplicate_same_access = 0x0000_0002
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    current_process = kernel32.GetCurrentProcess
    current_process.argtypes = ()
    current_process.restype = ctypes.c_void_p
    duplicate_handle = kernel32.DuplicateHandle
    duplicate_handle.argtypes = (
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint32,
    )
    duplicate_handle.restype = ctypes.c_int
    process = current_process()
    duplicated = ctypes.c_void_p()
    if not duplicate_handle(
        process,
        ctypes.c_void_p(handle.value),
        process,
        ctypes.byref(duplicated),
        0,
        0,
        duplicate_same_access,
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    raw = int(duplicated.value or 0)
    if raw == 0:
        raise OSError("handle duplication returned an invalid value")
    try:
        descriptor = msvcrt.open_osfhandle(raw, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        kernel32.CloseHandle(ctypes.c_void_p(raw))
        raise
    try:
        return os.fdopen(descriptor, "rb", buffering=0)
    except BaseException:
        os.close(descriptor)
        raise


def _duplicate_binary_stream(handle: WindowsHandle) -> BinaryIO:
    """Return a read/write binary stream over an authenticated stage handle."""

    import msvcrt

    duplicate_same_access = 0x0000_0002
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    current_process = kernel32.GetCurrentProcess
    current_process.argtypes = ()
    current_process.restype = ctypes.c_void_p
    duplicate_handle = kernel32.DuplicateHandle
    duplicate_handle.argtypes = (
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint32,
    )
    duplicate_handle.restype = ctypes.c_int
    process = current_process()
    duplicated = ctypes.c_void_p()
    if not duplicate_handle(
        process,
        ctypes.c_void_p(handle.value),
        process,
        ctypes.byref(duplicated),
        0,
        0,
        duplicate_same_access,
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    raw = int(duplicated.value or 0)
    if raw == 0:
        raise OSError("handle duplication returned an invalid value")
    try:
        descriptor = msvcrt.open_osfhandle(raw, os.O_RDWR | os.O_BINARY)
    except BaseException:
        kernel32.CloseHandle(ctypes.c_void_p(raw))
        raise
    try:
        return os.fdopen(descriptor, "r+b", buffering=0)
    except BaseException:
        os.close(descriptor)
        raise


def _map_generated_os_error(error: OSError) -> Never:
    try:
        _map_os_error(error, context="package_destination.generate")
    except ExportDestinationError as mapped:
        _map_single_destination_error(mapped)


def _map_single_destination_error(error: ExportDestinationError) -> Never:
    value = error.code.value
    if value.endswith("_CANCELLED"):
        reject_export_package_destination(
            ExportPackageDestinationErrorCode.CANCELLED,
            "package_destination.stage",
        )
    if value.endswith("_TIMED_OUT"):
        reject_export_package_destination(
            ExportPackageDestinationErrorCode.TIMED_OUT,
            "package_destination.stage",
        )
    if value.endswith("_DESTINATION_FULL"):
        reject_export_package_destination(
            ExportPackageDestinationErrorCode.DESTINATION_FULL,
            "package_destination.stage",
        )
    if value.endswith("_DESTINATION_DENIED"):
        reject_export_package_destination(
            ExportPackageDestinationErrorCode.DESTINATION_DENIED,
            "package_destination.stage",
        )
    reject_export_package_destination(
        ExportPackageDestinationErrorCode.STAGE_FAILED,
        "package_destination.stage",
    )


__all__ = [
    "BoundedPackageSource",
    "ExportPackageDestinationDependencies",
    "ExportPackageDestinationError",
    "ExportPackageDestinationErrorCode",
    "ExportPackageDestinationPhase",
    "ExportPackageDestinationResult",
    "GeneratedPackageWriter",
    "PackageArtifactCommitResult",
    "WindowsExportPackageTransaction",
    "reject_export_package_destination",
]

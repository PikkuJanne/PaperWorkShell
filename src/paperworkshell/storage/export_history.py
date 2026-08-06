"""Authenticated Windows persistence for path-free PDF export history."""

from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Final, Never
from uuid import UUID, uuid4

from paperworkshell.domain.pdf_export_history import (
    MAX_PDF_EXPORT_HISTORY_BYTES,
    PdfExportHistoryAppendResult,
    PdfExportHistoryAppendStatus,
    PdfExportHistoryDocument,
    PdfExportHistoryError,
    PdfExportHistoryErrorCode,
    PdfExportHistoryRecord,
    PdfExportHistorySnapshot,
    canonical_pdf_export_history_bytes,
    decode_pdf_export_history,
    reject_pdf_export_history,
)
from paperworkshell.storage._windows import (
    FileIdentity,
    WindowsHandle,
    open_handle,
    rename_open_file_no_replace_at,
    reopen_read_only_file,
    require_expected_handle,
    require_no_alternate_streams,
    write_new_file,
)
from paperworkshell.storage.errors import WorkspaceError, WorkspaceErrorCode
from paperworkshell.storage.quotas import QuotaArea
from paperworkshell.storage.workspace import CaseWorkspaceManager, WorkspaceCapacityReservation

_EXPORT_HISTORY_NAME: Final = "export-history.json"
_STAGE_SUFFIX: Final = ".tmp"
_RETIRED_SUFFIX: Final = ".retired"
_MAX_STAGE_ID_ATTEMPTS: Final = 16


class PdfExportHistoryPhase(StrEnum):
    """Deterministic interruption points for transaction tests and cancellation."""

    BEFORE_STAGE_WRITE = "before_stage_write"
    BEFORE_PUBLISH = "before_publish"
    AFTER_PUBLISH = "after_publish"


class _CommitState(StrEnum):
    COMMITTED = "committed"
    NOT_COMMITTED = "not_committed"
    UNKNOWN = "unknown"


class _FileLocation(StrEnum):
    INITIAL = "initial"
    TARGET = "target"
    UNKNOWN = "unknown"


HistoryCheckpoint = Callable[[PdfExportHistoryPhase], None]
HistoryStageWriter = Callable[[Path, bytes], WindowsHandle]
HistoryNewPublisher = Callable[[WindowsHandle, WindowsHandle, str], tuple[bool, int]]


def _noop_checkpoint(_phase: PdfExportHistoryPhase) -> None:
    return


def _publish_new(
    stage: WindowsHandle,
    case_root: WindowsHandle,
    destination_name: str,
) -> tuple[bool, int]:
    return rename_open_file_no_replace_at(
        stage,
        case_root,
        destination_name=destination_name,
    )


@dataclass(frozen=True, slots=True)
class PdfExportHistoryStorageDependencies:
    """Narrow deterministic seams; production defaults use retained Win32 handles."""

    checkpoint: HistoryCheckpoint = _noop_checkpoint
    write_stage: HistoryStageWriter = write_new_file
    publish_new: HistoryNewPublisher = _publish_new

    def __post_init__(self) -> None:
        if not all(
            callable(value)
            for value in (
                self.checkpoint,
                self.write_stage,
                self.publish_new,
            )
        ):
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.MODEL_INVALID,
                "export_history.dependencies",
            )


@dataclass(frozen=True, slots=True)
class _ObservedHistory:
    document: PdfExportHistoryDocument
    payload: bytes = field(repr=False)
    identity: FileIdentity = field(repr=False)


@dataclass(frozen=True, slots=True)
class _PublishOutcome:
    state: _CommitState
    delayed_exception: BaseException | None = field(default=None, repr=False)


class WindowsPdfExportHistoryRepository:
    """Append one record through atomic create-or-identity-resolved replacement."""

    __slots__ = ("_dependencies", "_id_factory", "_manager")

    def __init__(
        self,
        manager: CaseWorkspaceManager,
        *,
        id_factory: Callable[[], UUID] = uuid4,
        dependencies: PdfExportHistoryStorageDependencies | None = None,
    ) -> None:
        selected_dependencies = (
            PdfExportHistoryStorageDependencies() if dependencies is None else dependencies
        )
        if (
            not isinstance(manager, CaseWorkspaceManager)
            or not callable(id_factory)
            or type(selected_dependencies) is not PdfExportHistoryStorageDependencies
        ):
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.MODEL_INVALID,
                "export_history.repository",
            )
        self._manager = manager
        self._id_factory = id_factory
        self._dependencies = selected_dependencies

    def load(self, case_id: UUID) -> PdfExportHistorySnapshot:
        """Load one current canonical record file without following filesystem aliases."""

        selected = _require_case_id(case_id, context="export_history.load_case")
        try:
            with self._manager.authenticate_case(selected) as authority:
                with authority.transaction_guard():
                    observed = _read_history_if_present(authority.layout.export_history)
                    if observed is None:
                        document = PdfExportHistoryDocument()
                        payload = canonical_pdf_export_history_bytes(document)
                        digest = hashlib.sha256(payload).hexdigest()
                        token = hashlib.sha256(
                            b"paperworkshell-export-history-absent-v1\x00"
                            + selected.bytes
                            + bytes.fromhex(digest)
                        ).hexdigest()
                        return PdfExportHistorySnapshot(document, digest, token)
                    digest = hashlib.sha256(observed.payload).hexdigest()
                    return PdfExportHistorySnapshot(
                        observed.document,
                        digest,
                        _cas_token(observed.identity, digest),
                    )
        except PdfExportHistoryError:
            raise
        except WorkspaceError as error:
            _map_workspace_error(error, context="export_history.load_storage")
        except Exception:
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.STORAGE_FAILED,
                "export_history.load_storage",
            )

    def append(
        self,
        case_id: UUID,
        record: PdfExportHistoryRecord,
    ) -> PdfExportHistoryAppendResult:
        """Durably append first, leaving manifest linking to the application service."""

        selected = _require_case_id(case_id, context="export_history.append_case")
        if type(record) is not PdfExportHistoryRecord:
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.MODEL_INVALID,
                "export_history.append_record",
            )
        stage_handle: WindowsHandle | None = None
        expected_handle: WindowsHandle | None = None
        reservation: WorkspaceCapacityReservation | None = None
        retirement_reservation: WorkspaceCapacityReservation | None = None
        retired_path: Path | None = None
        state = _CommitState.NOT_COMMITTED
        try:
            with self._manager.authenticate_case(selected) as authority:
                with authority.transaction_guard():
                    current = _read_history_if_present(authority.layout.export_history)
                    base = PdfExportHistoryDocument() if current is None else current.document
                    candidate, appended = base.append(record)
                    if not appended:
                        return PdfExportHistoryAppendResult(
                            PdfExportHistoryAppendStatus.ALREADY_PRESENT,
                            record.export_id,
                            record.sha256,
                            len(base.records),
                        )
                    payload = canonical_pdf_export_history_bytes(candidate)
                    stage_id = _next_stage_id(self._id_factory)
                    stage_name = f"{stage_id}{_STAGE_SUFFIX}"
                    stage_path = authority.layout.temp / stage_name
                    retired_name: str | None = None
                    if current is not None:
                        retired_id = _next_stage_id(self._id_factory)
                        retired_name = f"{retired_id}{_RETIRED_SUFFIX}"
                        retired_path = authority.layout.temp / retired_name
                    reservation = self._manager.reserve_capacity(
                        selected,
                        QuotaArea.TEMP,
                        relative_path=f"temp/{stage_name}",
                        is_directory=False,
                        byte_count=len(payload),
                    )
                    if current is not None and retired_name is not None:
                        retirement_reservation = self._manager.reserve_capacity(
                            selected,
                            QuotaArea.TEMP,
                            relative_path=f"temp/{retired_name}",
                            is_directory=False,
                            byte_count=len(current.payload),
                            entry_count=0,
                        )
                    with reservation.publication_guard(
                        case_identity=authority.identity,
                        relative_parts=reservation.relative_parts,
                        is_directory=False,
                        byte_count=len(payload),
                        entry_count=1,
                    ):
                        with ExitStack() as publication_stack:
                            if retirement_reservation is not None and current is not None:
                                publication_stack.enter_context(
                                    retirement_reservation.publication_guard(
                                        case_identity=authority.identity,
                                        relative_parts=retirement_reservation.relative_parts,
                                        is_directory=False,
                                        byte_count=len(current.payload),
                                        entry_count=0,
                                    )
                                )
                            self._dependencies.checkpoint(PdfExportHistoryPhase.BEFORE_STAGE_WRITE)
                            stage_handle = self._dependencies.write_stage(stage_path, payload)
                            _authenticate_stage(stage_handle, stage_path, payload)
                            self._dependencies.checkpoint(PdfExportHistoryPhase.BEFORE_PUBLISH)
                            if current is None:
                                publication = self._publish_first(
                                    stage_handle,
                                    authority.handle,
                                    authority.layout.export_history,
                                    stage_path,
                                )
                            else:
                                if retired_name is None or retired_path is None:
                                    reject_pdf_export_history(
                                        PdfExportHistoryErrorCode.STORAGE_FAILED,
                                        "export_history.retirement_plan",
                                    )
                                expected_handle = _open_expected_history(
                                    authority.layout.export_history,
                                    current,
                                )
                                temp_root = publication_stack.enter_context(
                                    open_handle(authority.layout.temp, traverse=True)
                                )
                                _authenticate_directory_handle(
                                    temp_root,
                                    authority.layout.temp,
                                )
                                publication = self._publish_replacement(
                                    stage_handle,
                                    expected_handle,
                                    authority.handle,
                                    temp_root,
                                    authority.layout.export_history,
                                    stage_path,
                                    retired_path,
                                    retired_name,
                                )
                            state = publication.state
                            if publication.delayed_exception is not None:
                                if not isinstance(publication.delayed_exception, Exception):
                                    raise publication.delayed_exception
                                if state is _CommitState.UNKNOWN:
                                    reject_pdf_export_history(
                                        PdfExportHistoryErrorCode.COMMIT_UNCERTAIN,
                                        "export_history.commit_uncertain",
                                    )
                                if state is _CommitState.NOT_COMMITTED:
                                    raise publication.delayed_exception
                            if state is _CommitState.UNKNOWN:
                                reject_pdf_export_history(
                                    PdfExportHistoryErrorCode.COMMIT_UNCERTAIN,
                                    "export_history.commit_uncertain",
                                )
                            if state is not _CommitState.COMMITTED:
                                reject_pdf_export_history(
                                    PdfExportHistoryErrorCode.STORAGE_FAILED,
                                    "export_history.commit_failed",
                                )
                            self._dependencies.checkpoint(PdfExportHistoryPhase.AFTER_PUBLISH)
                            committed_identity = stage_handle.info.identity
                            _close_handle(stage_handle, delete=False)
                            stage_handle = None
                            _verify_committed_history(
                                authority.layout.export_history,
                                candidate,
                                payload,
                                committed_identity,
                            )
                            if expected_handle is not None and retired_path is not None:
                                _remove_retired_history(expected_handle, retired_path)
                                expected_handle = None
                            return PdfExportHistoryAppendResult(
                                PdfExportHistoryAppendStatus.PUBLISHED,
                                record.export_id,
                                record.sha256,
                                len(candidate.records),
                            )
        except PdfExportHistoryError:
            raise
        except WorkspaceError as error:
            _map_workspace_error(error, context="export_history.append_storage")
        except Exception:
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.STORAGE_FAILED,
                "export_history.append_storage",
            )
        finally:
            _close_handle(expected_handle, delete=False)
            _close_handle(stage_handle, delete=state is _CommitState.NOT_COMMITTED)
            if retirement_reservation is not None:
                retirement_reservation.release()
            if reservation is not None:
                reservation.release()

    def _publish_first(
        self,
        stage: WindowsHandle,
        case_root: WindowsHandle,
        destination: Path,
        stage_path: Path,
    ) -> _PublishOutcome:
        candidate_identity = stage.info.identity
        delayed: BaseException | None = None
        try:
            renamed, _error_number = self._dependencies.publish_new(
                stage,
                case_root,
                _EXPORT_HISTORY_NAME,
            )
        except BaseException as error:
            delayed = error
            renamed = False
        location = _resolve_file_location(
            stage,
            expected_identity=candidate_identity,
            initial_path=stage_path,
            target_path=destination,
        )
        if location is _FileLocation.TARGET:
            return _PublishOutcome(_CommitState.COMMITTED, delayed)
        if delayed is not None:
            if location is _FileLocation.INITIAL:
                return _PublishOutcome(_CommitState.NOT_COMMITTED, delayed)
            return _PublishOutcome(_CommitState.UNKNOWN, delayed)
        if location is _FileLocation.INITIAL and not renamed:
            return _PublishOutcome(_CommitState.NOT_COMMITTED)
        return _PublishOutcome(_CommitState.UNKNOWN)

    def _publish_replacement(
        self,
        stage: WindowsHandle,
        expected: WindowsHandle,
        case_root: WindowsHandle,
        temp_root: WindowsHandle,
        destination: Path,
        stage_path: Path,
        retired_path: Path,
        retired_name: str,
    ) -> _PublishOutcome:
        candidate_identity = stage.info.identity
        expected_identity = expected.info.identity
        try:
            _authenticate_history_handle(expected, destination)
        except Exception:
            return _PublishOutcome(_CommitState.UNKNOWN)
        if (
            _resolve_file_location(
                stage,
                expected_identity=candidate_identity,
                initial_path=stage_path,
                target_path=destination,
            )
            is not _FileLocation.INITIAL
        ):
            return _PublishOutcome(_CommitState.UNKNOWN)

        retirement_delayed: BaseException | None = None
        try:
            retired, _error_number = self._dependencies.publish_new(
                expected,
                temp_root,
                retired_name,
            )
        except BaseException as error:
            retirement_delayed = error
            retired = False
        expected_location = _resolve_file_location(
            expected,
            expected_identity=expected_identity,
            initial_path=destination,
            target_path=retired_path,
        )
        if expected_location is _FileLocation.INITIAL:
            if retirement_delayed is not None or not retired:
                return _PublishOutcome(_CommitState.NOT_COMMITTED, retirement_delayed)
            return _PublishOutcome(_CommitState.UNKNOWN)
        if expected_location is not _FileLocation.TARGET:
            return _PublishOutcome(_CommitState.UNKNOWN, retirement_delayed)
        if retirement_delayed is not None:
            return self._restore_expected(
                expected,
                case_root,
                destination,
                retired_path,
                expected_identity,
                delayed=retirement_delayed,
            )

        publication_delayed: BaseException | None = None
        try:
            published, _error_number = self._dependencies.publish_new(
                stage,
                case_root,
                _EXPORT_HISTORY_NAME,
            )
        except BaseException as error:
            publication_delayed = error
            published = False
        candidate_location = _resolve_file_location(
            stage,
            expected_identity=candidate_identity,
            initial_path=stage_path,
            target_path=destination,
        )
        if candidate_location is _FileLocation.TARGET:
            return _PublishOutcome(_CommitState.COMMITTED, publication_delayed)
        if candidate_location is not _FileLocation.INITIAL:
            return _PublishOutcome(_CommitState.UNKNOWN, publication_delayed)
        if published:
            return _PublishOutcome(_CommitState.UNKNOWN, publication_delayed)
        return self._restore_expected(
            expected,
            case_root,
            destination,
            retired_path,
            expected_identity,
            delayed=publication_delayed,
        )

    def _restore_expected(
        self,
        expected: WindowsHandle,
        case_root: WindowsHandle,
        destination: Path,
        retired_path: Path,
        expected_identity: FileIdentity,
        *,
        delayed: BaseException | None,
    ) -> _PublishOutcome:
        restoration_delayed: BaseException | None = None
        try:
            _restored, _error_number = self._dependencies.publish_new(
                expected,
                case_root,
                _EXPORT_HISTORY_NAME,
            )
        except BaseException as error:
            restoration_delayed = error
        location = _resolve_file_location(
            expected,
            expected_identity=expected_identity,
            initial_path=retired_path,
            target_path=destination,
        )
        selected_error = delayed if delayed is not None else restoration_delayed
        if location is _FileLocation.TARGET:
            return _PublishOutcome(_CommitState.NOT_COMMITTED, selected_error)
        return _PublishOutcome(_CommitState.UNKNOWN, selected_error)


def _read_history_if_present(path: Path) -> _ObservedHistory | None:
    if not os.path.lexists(path):
        return None
    try:
        with open_handle(path, read=True) as handle:
            _authenticate_history_handle(handle, path)
            payload = handle.read_bytes(maximum_bytes=MAX_PDF_EXPORT_HISTORY_BYTES)
            _authenticate_history_handle(handle, path)
            document = decode_pdf_export_history(payload)
            return _ObservedHistory(document, payload, handle.info.identity)
    except PdfExportHistoryError:
        raise
    except WorkspaceError:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.DOCUMENT_INVALID,
            "export_history.record_identity",
        )


def _open_expected_history(path: Path, expected: _ObservedHistory) -> WindowsHandle:
    handle: WindowsHandle | None = None
    try:
        handle = open_handle(path, read=True, delete=True, share_delete=True)
        _authenticate_history_handle(handle, path)
        if handle.info.identity != expected.identity:
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.COMMIT_UNCERTAIN,
                "export_history.expected_identity",
            )
        payload = handle.read_bytes(maximum_bytes=MAX_PDF_EXPORT_HISTORY_BYTES)
        _authenticate_history_handle(handle, path)
        if not hmac.compare_digest(payload, expected.payload):
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.COMMIT_UNCERTAIN,
                "export_history.expected_payload",
            )
        handle.rewind()
        return handle
    except BaseException:
        _close_handle(handle, delete=False)
        raise


def _authenticate_history_handle(handle: WindowsHandle, expected_path: Path) -> None:
    current = handle.refresh_info()
    require_expected_handle(handle, expected_path, directory=False)
    if current.is_reparse_point or current.is_sparse or current.link_count != 1:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.DOCUMENT_INVALID,
            "export_history.record_identity",
        )
    require_no_alternate_streams(current.final_path)


def _authenticate_stage(stage: WindowsHandle, expected_path: Path, expected_payload: bytes) -> None:
    try:
        current = stage.refresh_info()
        require_expected_handle(stage, expected_path, directory=False)
        if (
            current.is_reparse_point
            or current.is_sparse
            or current.link_count != 1
            or current.logical_size != len(expected_payload)
        ):
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.STORAGE_FAILED,
                "export_history.stage_identity",
            )
        require_no_alternate_streams(current.final_path)
        with reopen_read_only_file(stage) as reader:
            observed = reader.read_bytes(maximum_bytes=MAX_PDF_EXPORT_HISTORY_BYTES)
        current = stage.refresh_info()
        require_expected_handle(stage, expected_path, directory=False)
        if (
            current.is_reparse_point
            or current.is_sparse
            or current.link_count != 1
            or current.logical_size != len(expected_payload)
        ):
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.STORAGE_FAILED,
                "export_history.stage_identity",
            )
        if not hmac.compare_digest(observed, expected_payload):
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.STORAGE_FAILED,
                "export_history.stage_payload",
            )
        decode_pdf_export_history(observed)
    except PdfExportHistoryError:
        raise
    except WorkspaceError:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.STORAGE_FAILED,
            "export_history.stage_identity",
        )


def _resolve_file_location(
    handle: WindowsHandle,
    *,
    expected_identity: FileIdentity,
    initial_path: Path,
    target_path: Path,
) -> _FileLocation:
    try:
        current = handle.refresh_info()
    except BaseException:
        return _FileLocation.UNKNOWN
    if (
        current.identity != expected_identity
        or current.is_directory
        or current.is_reparse_point
        or current.is_sparse
        or current.link_count != 1
    ):
        return _FileLocation.UNKNOWN
    if _same_path(current.final_path, target_path):
        return _FileLocation.TARGET
    if _same_path(current.final_path, initial_path):
        return _FileLocation.INITIAL
    return _FileLocation.UNKNOWN


def _authenticate_directory_handle(handle: WindowsHandle, expected_path: Path) -> None:
    current = handle.refresh_info()
    require_expected_handle(handle, expected_path, directory=True)
    if current.is_reparse_point:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.STORAGE_FAILED,
            "export_history.temp_identity",
        )
    require_no_alternate_streams(current.final_path)


def _verify_committed_history(
    destination: Path,
    expected_document: PdfExportHistoryDocument,
    expected_payload: bytes,
    expected_identity: FileIdentity,
) -> None:
    try:
        with open_handle(destination, read=True) as current:
            _authenticate_history_handle(current, destination)
            if current.info.identity != expected_identity:
                reject_pdf_export_history(
                    PdfExportHistoryErrorCode.COMMIT_UNCERTAIN,
                    "export_history.verify_identity",
                )
            payload = current.read_bytes(maximum_bytes=MAX_PDF_EXPORT_HISTORY_BYTES)
            _authenticate_history_handle(current, destination)
            document = decode_pdf_export_history(payload)
            if document != expected_document or not hmac.compare_digest(payload, expected_payload):
                reject_pdf_export_history(
                    PdfExportHistoryErrorCode.COMMIT_UNCERTAIN,
                    "export_history.verify_payload",
                )
    except PdfExportHistoryError:
        raise
    except WorkspaceError:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.COMMIT_UNCERTAIN,
            "export_history.verify_storage",
        )


def _remove_retired_history(handle: WindowsHandle, retired_path: Path) -> None:
    """Delete only the exact retired identity after the replacement is verified."""

    expected_identity = handle.info.identity
    _close_handle(handle, delete=False)
    cleanup: WindowsHandle | None = None
    try:
        cleanup = open_handle(retired_path, delete=True)
        current = cleanup.refresh_info()
        require_expected_handle(
            cleanup,
            retired_path,
            directory=False,
            expected_identity=expected_identity,
        )
        if current.is_reparse_point or current.is_sparse or current.link_count != 1:
            return
        require_no_alternate_streams(current.final_path)
        cleanup.mark_for_deletion()
    except BaseException:
        return
    finally:
        _close_handle(cleanup, delete=False)


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _cas_token(identity: FileIdentity, digest: str) -> str:
    material = (
        identity.volume_serial_number.to_bytes(8, byteorder="big", signed=False)
        + identity.file_id
        + bytes.fromhex(digest)
    )
    return hashlib.sha256(material).hexdigest()


def _next_stage_id(factory: Callable[[], UUID]) -> UUID:
    for _attempt in range(_MAX_STAGE_ID_ATTEMPTS):
        try:
            value = factory()
        except BaseException:
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.STORAGE_FAILED,
                "export_history.stage_id",
            )
        if type(value) is UUID and value.version == 4:
            return value
    reject_pdf_export_history(
        PdfExportHistoryErrorCode.STORAGE_FAILED,
        "export_history.stage_id",
    )


def _require_case_id(value: object, *, context: str) -> UUID:
    if type(value) is not UUID or value.version != 4:
        reject_pdf_export_history(PdfExportHistoryErrorCode.MODEL_INVALID, context)
    return value


def _map_workspace_error(error: WorkspaceError, *, context: str) -> Never:
    if error.code is WorkspaceErrorCode.QUOTA_EXCEEDED:
        reject_pdf_export_history(PdfExportHistoryErrorCode.CAPACITY_EXCEEDED, context)
    if error.code in {
        WorkspaceErrorCode.ALIAS_DETECTED,
        WorkspaceErrorCode.REPARSE_POINT,
        WorkspaceErrorCode.IDENTITY_CHANGED,
        WorkspaceErrorCode.PERMISSIONS_INSECURE,
    }:
        reject_pdf_export_history(PdfExportHistoryErrorCode.DOCUMENT_INVALID, context)
    reject_pdf_export_history(PdfExportHistoryErrorCode.STORAGE_FAILED, context)


def _close_handle(handle: WindowsHandle | None, *, delete: bool) -> None:
    if handle is None:
        return
    if delete:
        try:
            handle.mark_for_deletion()
        except BaseException:
            pass
    try:
        handle.close()
    except BaseException:
        pass

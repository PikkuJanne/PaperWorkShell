"""Authenticated Windows adapter for immutable source ingest and integrity checks."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Final, Never, Self, cast
from uuid import UUID, uuid4

from paperworkshell.domain.case_lifecycle import (
    MAX_SAFE_MANIFEST_INTEGER,
    CaseLifecycleError,
    CaseLifecycleErrorCode,
)
from paperworkshell.domain.import_precheck import (
    DetectedMediaType,
    ImportPrecheckError,
    ImportPrecheckErrorCode,
)
from paperworkshell.domain.source_ingest import (
    MAX_SOURCE_PAGES,
    CompletedSourceInspection,
    DuplicateContentSignal,
    PreparedSourceSummary,
    SourceEncryptionState,
    SourceImportProgress,
    SourceImportResult,
    SourceIngestError,
    SourceIngestErrorCode,
    SourceIntegrityResult,
    SourceIntegrityStatus,
    SourceProgressPhase,
    SourceSignatureState,
    reject_source_ingest,
    require_internal_id,
    require_source_display_name,
)
from paperworkshell.storage._windows import (
    MAXIMUM_STREAM_CHUNK_BYTES,
    FileIdentity,
    ManifestReplaceState,
    WindowsHandle,
    WindowsPendingSourceFile,
    create_pending_source_file,
    open_handle,
    publish_new_original,
    replace_manifest_atomically,
    require_expected_handle,
    require_no_alternate_streams,
    verify_private_acl,
    write_new_file,
)
from paperworkshell.storage.case_lifecycle import (
    MAX_MANIFEST_BYTES,
    MAX_MANIFEST_COLLECTION_ITEMS,
    _decode_manifest,
    _parse_utc_timestamp,
    _render_utc_timestamp,
    _summary_from_manifest,
)
from paperworkshell.storage.errors import WorkspaceError, WorkspaceErrorCode
from paperworkshell.storage.quotas import QuotaArea
from paperworkshell.storage.source_precheck import WindowsSourcePrechecker
from paperworkshell.storage.workspace import (
    AuthenticatedCase,
    CaseWorkspaceManager,
    WorkspaceCapacityReservation,
)

_MAXIMUM_IDENTIFIER_ATTEMPTS: Final = 32
_SHA256_RE: Final = re.compile(r"[0-9a-f]{64}\Z")
_UUID_RE: Final = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
_SOURCE_RECORD_KEYS: Final = frozenset(
    {
        "source_id",
        "display_name",
        "storage_name",
        "detected_media_type",
        "byte_size",
        "page_count",
        "sha256",
        "signature_state",
        "encryption_state",
        "import_state",
        "imported_at",
    }
)
_PAGE_RECORD_KEYS: Final = frozenset({"page_id", "source_id", "source_page_index"})
_EMPTY_IMPORT_COLLECTIONS: Final = (
    "page_operations",
    "assets",
    "overlays",
    "assignments",
    "confirmations",
)
_INITIAL_CONSTRAINTS: Final[dict[str, object]] = {
    "constraints_version": 1,
    "allowed_output_modes": ["combined_pdf"],
    "encryption_policy": "must_be_unencrypted",
}
_INITIAL_EXPORT_HISTORY: Final[dict[str, object]] = {
    "storage_name": "export-history.json",
    "entries": [],
}
_PDF_SIGNATURE_STATES: Final = frozenset(
    {
        SourceSignatureState.NONE_DETECTED.value,
        SourceSignatureState.MARKERS_DETECTED.value,
        SourceSignatureState.UNKNOWN.value,
    }
)
_PDF_ENCRYPTION_STATES: Final = frozenset(
    {
        SourceEncryptionState.UNENCRYPTED.value,
        SourceEncryptionState.ENCRYPTED.value,
        SourceEncryptionState.UNKNOWN.value,
    }
)
_CONSERVATIVE_TIMESTAMP: Final = "9999-12-31T23:59:59.999999Z"
_PLACEHOLDER_PAGE_ID: Final = "00000000-0000-4000-8000-000000000000"

CancellationProbe = Callable[[], bool]
ProgressSink = Callable[[SourceImportProgress], None]


@dataclass(frozen=True, slots=True)
class _StoredSource:
    source_id: UUID
    storage_name: str
    media_type: DetectedMediaType
    byte_size: int
    page_count: int
    sha256: str = field(repr=False)
    imported_at: datetime


@dataclass(frozen=True, slots=True)
class _ManifestSnapshot:
    payload: bytes = field(repr=False)
    identity: FileIdentity
    document: dict[str, object] = field(repr=False)
    revision: int
    created_at: datetime
    updated_at: datetime
    sources: tuple[_StoredSource, ...] = field(repr=False)
    page_ids: tuple[UUID, ...]
    identifiers: frozenset[UUID] = field(repr=False)


class WindowsPreparedSourceLease(AbstractContextManager["WindowsPreparedSourceLease"]):
    """Retain one verified pending copy until source-first/manifest-last commit."""

    __slots__ = (
        "_authority",
        "_closed",
        "_display_name",
        "_manifest_reservation",
        "_manifest_stage_path",
        "_pending",
        "_snapshot",
        "_source_reservation",
        "_store",
        "_summary",
    )

    def __init__(
        self,
        *,
        store: WindowsSourceIngestStore,
        authority: AuthenticatedCase,
        summary: PreparedSourceSummary,
        display_name: str,
        snapshot: _ManifestSnapshot,
        pending: WindowsPendingSourceFile,
        source_reservation: WorkspaceCapacityReservation,
        manifest_reservation: WorkspaceCapacityReservation,
        manifest_stage_path: Path,
    ) -> None:
        self._store = store
        self._authority = authority
        self._summary = summary
        self._display_name = display_name
        self._snapshot = snapshot
        self._pending = pending
        self._source_reservation = source_reservation
        self._manifest_reservation = manifest_reservation
        self._manifest_stage_path = manifest_stage_path
        self._closed = False

    @property
    def summary(self) -> PreparedSourceSummary:
        return self._summary

    def __enter__(self) -> Self:
        if self._closed:
            reject_source_ingest(SourceIngestErrorCode.PREPARE_FAILED, "source.pending_closed")
        return self

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.abort()

    def commit(
        self,
        inspection: CompletedSourceInspection,
        *,
        cancellation: CancellationProbe,
        progress: ProgressSink,
    ) -> SourceImportResult:
        """Publish the exact source first and replace the complete manifest last."""

        if self._closed:
            reject_source_ingest(SourceIngestErrorCode.COMMIT_FAILED, "source.commit_closed")
        return self._store._commit_prepared(
            self,
            inspection,
            cancellation=cancellation,
            progress=progress,
        )

    def abort(self) -> None:
        """Remove only the exact retained pending object, then release all authority."""

        if self._closed:
            return
        self._closed = True
        _delete_pending_no_raise(self._pending)
        self._release()

    def _complete(self, *, preserve_source: bool) -> None:
        if self._closed:
            return
        self._closed = True
        if preserve_source:
            _close_pending_no_raise(self._pending)
        else:
            _delete_pending_no_raise(self._pending)
        self._release()

    def _release(self) -> None:
        try:
            self._manifest_reservation.release()
        finally:
            try:
                self._source_reservation.release()
            finally:
                try:
                    self._authority.close()
                except Exception:
                    pass


class WindowsSourceIngestStore:
    """Prepare, atomically commit, and later verify immutable case sources."""

    __slots__ = (
        "_clock",
        "_id_factory",
        "_manager",
        "_maximum_page_count",
        "_prechecker",
        "_temp_id_factory",
    )

    def __init__(
        self,
        manager: CaseWorkspaceManager,
        prechecker: WindowsSourcePrechecker,
        *,
        maximum_page_count: int,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], UUID] = uuid4,
        temp_id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if (
            type(maximum_page_count) is not int
            or not 1 <= maximum_page_count <= MAX_SOURCE_PAGES
            or not callable(id_factory)
            or not callable(temp_id_factory)
        ):
            reject_source_ingest(SourceIngestErrorCode.MODEL_INVALID, "source.store_config")
        self._manager = manager
        self._prechecker = prechecker
        self._maximum_page_count = maximum_page_count
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory
        self._temp_id_factory = temp_id_factory

    def prepare(
        self,
        case_id: UUID,
        source_path: Path,
        display_name: str,
        *,
        cancellation: CancellationProbe,
        progress: ProgressSink,
    ) -> WindowsPreparedSourceLease:
        """Copy through the one live precheck handle and return after terminal recheck."""

        selected_case = require_internal_id(case_id, context="source.prepare_case")
        safe_display_name = require_source_display_name(display_name)
        if not isinstance(source_path, Path):
            reject_source_ingest(SourceIngestErrorCode.SOURCE_PATH_INVALID, "source.prepare_path")
        _require_not_cancelled(cancellation, context="source.prepare_cancel")

        authority: AuthenticatedCase | None = None
        pending: WindowsPendingSourceFile | None = None
        source_reservation: WorkspaceCapacityReservation | None = None
        manifest_reservation: WorkspaceCapacityReservation | None = None
        prepared = False
        try:
            authority = self._manager.authenticate_case(selected_case, retain_lease=True)
            with _open_manifest_snapshot(authority) as (snapshot, _manifest_handle):
                used_ids = set(snapshot.identifiers)
                used_ids.add(selected_case)
                pending_id = _allocate_identifier(
                    self._temp_id_factory,
                    used_ids,
                    context="source.pending_id",
                )
                source_id = _allocate_identifier(
                    self._id_factory,
                    used_ids,
                    context="source.source_id",
                )
                manifest_stage_id = _allocate_identifier(
                    self._temp_id_factory,
                    used_ids,
                    context="source.manifest_stage_id",
                )

            with self._prechecker.open(source_path) as source:
                result = source.result
                maximum_pages = min(
                    self._maximum_page_count,
                    MAX_MANIFEST_COLLECTION_ITEMS - len(snapshot.page_ids),
                )
                if len(snapshot.sources) >= MAX_MANIFEST_COLLECTION_ITEMS or maximum_pages < 1:
                    reject_source_ingest(
                        SourceIngestErrorCode.CAPACITY_EXCEEDED,
                        "source.manifest_capacity",
                    )
                manifest_upper_bound = _candidate_manifest_upper_bound(
                    snapshot,
                    source_id=source_id,
                    display_name=safe_display_name,
                    media_type=result.media_type,
                    byte_size=result.byte_size,
                    maximum_page_count=maximum_pages,
                )
                source_stage_path = authority.layout.temp / f"{pending_id}.tmp"
                manifest_stage_path = authority.layout.temp / f"{manifest_stage_id}.tmp"
                source_reservation = self._manager.reserve_capacity(
                    selected_case,
                    QuotaArea.TEMP,
                    relative_path=f"temp/{pending_id}.tmp",
                    is_directory=False,
                    byte_count=result.byte_size,
                )
                manifest_reservation = self._manager.reserve_capacity(
                    selected_case,
                    QuotaArea.TEMP,
                    relative_path=f"temp/{manifest_stage_id}.tmp",
                    is_directory=False,
                    byte_count=manifest_upper_bound,
                )
                with open_handle(authority.layout.temp) as temp_root:
                    require_expected_handle(temp_root, authority.layout.temp, directory=True)
                    verify_private_acl(temp_root)
                    require_no_alternate_streams(authority.layout.temp)
                    with source_reservation.publication_guard(
                        case_identity=authority.identity,
                        relative_parts=("temp", f"{pending_id}.tmp"),
                        is_directory=False,
                        byte_count=result.byte_size,
                        entry_count=1,
                    ):
                        pending = create_pending_source_file(
                            source_stage_path,
                            temp_root=temp_root,
                        )
                        digest = _copy_and_verify_source(
                            source,
                            pending,
                            operation_id=pending_id,
                            byte_size=result.byte_size,
                            cancellation=cancellation,
                            progress=progress,
                        )
                summary = PreparedSourceSummary(
                    pending_id=pending_id,
                    case_id=selected_case,
                    source_id=source_id,
                    media_type=result.media_type,
                    byte_size=result.byte_size,
                    sha256=digest,
                )
            prepared = True
            if (
                authority is None
                or pending is None
                or source_reservation is None
                or manifest_reservation is None
            ):
                reject_source_ingest(
                    SourceIngestErrorCode.PREPARE_FAILED,
                    "source.prepare_result",
                )
            return WindowsPreparedSourceLease(
                store=self,
                authority=authority,
                summary=summary,
                display_name=safe_display_name,
                snapshot=snapshot,
                pending=pending,
                source_reservation=source_reservation,
                manifest_reservation=manifest_reservation,
                manifest_stage_path=manifest_stage_path,
            )
        except SourceIngestError:
            raise
        except ImportPrecheckError as error:
            _map_precheck_error(error)
        except CaseLifecycleError as error:
            _map_manifest_error(error)
        except WorkspaceError as error:
            _map_workspace_error(error, preparing=True)
        except Exception:
            reject_source_ingest(SourceIngestErrorCode.PREPARE_FAILED, "source.prepare_storage")
        finally:
            if not prepared:
                if pending is not None:
                    _delete_pending_no_raise(pending)
                if manifest_reservation is not None:
                    manifest_reservation.release()
                if source_reservation is not None:
                    source_reservation.release()
                if authority is not None:
                    try:
                        authority.close()
                    except Exception:
                        pass

    def verify_integrity(
        self,
        case_id: UUID,
        source_id: UUID,
        *,
        cancellation: CancellationProbe,
        progress: ProgressSink,
    ) -> SourceIntegrityResult:
        """Stream one committed source through an authenticated read-only handle."""

        selected_case = require_internal_id(case_id, context="integrity.case_id")
        selected_source = require_internal_id(source_id, context="integrity.source_id")
        _require_not_cancelled(cancellation, context="integrity.cancel")
        try:
            with self._manager.authenticate_case(selected_case) as authority:
                with _open_manifest_snapshot(authority) as (snapshot, _manifest_handle):
                    record = next(
                        (item for item in snapshot.sources if item.source_id == selected_source),
                        None,
                    )
                if record is None:
                    reject_source_ingest(
                        SourceIngestErrorCode.VERIFY_FAILED,
                        "integrity.record_missing",
                    )
                with open_handle(authority.layout.originals) as originals_root:
                    require_expected_handle(
                        originals_root,
                        authority.layout.originals,
                        directory=True,
                    )
                    verify_private_acl(originals_root)
                    require_no_alternate_streams(authority.layout.originals)
                    source_path = authority.layout.originals / record.storage_name
                    if not os.path.lexists(source_path):
                        return SourceIntegrityResult(
                            selected_case,
                            selected_source,
                            SourceIntegrityStatus.MISSING,
                        )
                    try:
                        with open_handle(source_path, read=True) as source_handle:
                            require_expected_handle(source_handle, source_path, directory=False)
                            info = source_handle.info
                            if (
                                info.is_reparse_point
                                or info.is_sparse
                                or info.link_count != 1
                                or info.logical_size != record.byte_size
                            ):
                                return _mismatch(selected_case, selected_source)
                            require_no_alternate_streams(source_path)
                            source_handle.rewind()
                            digest = hashlib.sha256()
                            completed = 0
                            while completed < record.byte_size:
                                _require_not_cancelled(
                                    cancellation,
                                    context="integrity.cancel",
                                )
                                count = min(
                                    MAXIMUM_STREAM_CHUNK_BYTES,
                                    record.byte_size - completed,
                                )
                                digest.update(source_handle.read_exact(count))
                                completed += count
                                _report_progress(
                                    progress,
                                    SourceImportProgress(
                                        selected_source,
                                        SourceProgressPhase.VERIFYING_INTEGRITY,
                                        completed,
                                        record.byte_size,
                                    ),
                                )
                            current = source_handle.require_eof()
                            if (
                                current.identity != info.identity
                                or current.logical_size != record.byte_size
                                or digest.hexdigest() != record.sha256
                            ):
                                return _mismatch(selected_case, selected_source)
                    except WorkspaceError:
                        return _mismatch(selected_case, selected_source)
                return SourceIntegrityResult(
                    selected_case,
                    selected_source,
                    SourceIntegrityStatus.VERIFIED,
                )
        except SourceIngestError:
            raise
        except CaseLifecycleError as error:
            _map_manifest_error(error)
        except WorkspaceError:
            reject_source_ingest(SourceIngestErrorCode.VERIFY_FAILED, "integrity.storage")
        except Exception:
            reject_source_ingest(SourceIngestErrorCode.VERIFY_FAILED, "integrity.storage")

    def _commit_prepared(
        self,
        prepared: WindowsPreparedSourceLease,
        inspection: CompletedSourceInspection,
        *,
        cancellation: CancellationProbe,
        progress: ProgressSink,
    ) -> SourceImportResult:
        summary = prepared._summary
        if (
            type(inspection) is not CompletedSourceInspection
            or inspection.pending_id != summary.pending_id
            or inspection.media_type is not summary.media_type
            or inspection.page_count > self._maximum_page_count
        ):
            reject_source_ingest(
                SourceIngestErrorCode.INSPECTION_MISMATCH,
                "source.commit_inspection",
            )
        _require_not_cancelled(cancellation, context="source.commit_cancel")
        manifest_handle: WindowsHandle | None = None
        source_published = False
        preserve_source = False
        preserve_manifest_candidate = False
        committed_result: SourceImportResult | None = None
        failure: BaseException | None = None
        page_ids: tuple[UUID, ...] = ()
        duplicates: tuple[UUID, ...] = ()
        try:
            page_ids = _allocate_page_ids(
                self._id_factory,
                prepared._snapshot.identifiers | {summary.source_id},
                inspection.page_count,
            )
            candidate, duplicates = _build_candidate_manifest(
                prepared._snapshot,
                summary=summary,
                display_name=prepared._display_name,
                inspection=inspection,
                page_ids=page_ids,
                timestamp=self._clock(),
            )
            if len(candidate) > prepared._manifest_reservation.byte_count:
                reject_source_ingest(
                    SourceIngestErrorCode.CAPACITY_EXCEEDED,
                    "source.manifest_reservation",
                )
            _require_not_cancelled(cancellation, context="source.commit_cancel")
            _report_progress(
                progress,
                SourceImportProgress(
                    summary.pending_id,
                    SourceProgressPhase.COMMITTING,
                    0,
                    1,
                ),
            )
            authority = prepared._authority
            source_parts = prepared._source_reservation.relative_parts
            manifest_parts = prepared._manifest_reservation.relative_parts
            with prepared._source_reservation.publication_guard(
                case_identity=authority.identity,
                relative_parts=source_parts,
                is_directory=False,
                byte_count=summary.byte_size,
                entry_count=1,
            ):
                with prepared._manifest_reservation.publication_guard(
                    case_identity=authority.identity,
                    relative_parts=manifest_parts,
                    is_directory=False,
                    byte_count=len(candidate),
                    entry_count=1,
                ):
                    with open_handle(authority.layout.temp) as temp_root:
                        require_expected_handle(temp_root, authority.layout.temp, directory=True)
                        verify_private_acl(temp_root)
                        require_no_alternate_streams(authority.layout.temp)
                        require_expected_handle(
                            prepared._pending.handle,
                            authority.layout.temp / f"{summary.pending_id}.tmp",
                            directory=False,
                        )
                        old_snapshot, expected_manifest = _read_retained_manifest_snapshot(
                            authority,
                            share_delete=True,
                            delete=True,
                        )
                        try:
                            if (
                                old_snapshot.identity != prepared._snapshot.identity
                                or old_snapshot.payload != prepared._snapshot.payload
                            ):
                                reject_source_ingest(
                                    SourceIngestErrorCode.MANIFEST_CONFLICT,
                                    "source.manifest_cas",
                                )
                            manifest_handle = write_new_file(
                                prepared._manifest_stage_path,
                                candidate,
                            )
                            require_no_alternate_streams(prepared._manifest_stage_path)
                            _require_not_cancelled(
                                cancellation,
                                context="source.commit_cancel",
                            )
                            with open_handle(authority.layout.originals) as originals_root:
                                require_expected_handle(
                                    originals_root,
                                    authority.layout.originals,
                                    directory=True,
                                )
                                verify_private_acl(originals_root)
                                require_no_alternate_streams(authority.layout.originals)
                                publish_new_original(
                                    prepared._pending,
                                    originals_root,
                                    storage_name=f"{summary.source_id}.bin",
                                )
                                source_published = True
                                replace_result = replace_manifest_atomically(
                                    manifest_handle,
                                    authority.handle,
                                    expected_manifest,
                                )
                        finally:
                            expected_manifest.close()
                    if replace_result.state is ManifestReplaceState.COMMITTED:
                        preserve_source = True
                        committed_result = SourceImportResult(
                            case_id=summary.case_id,
                            source_id=summary.source_id,
                            page_ids=page_ids,
                            media_type=summary.media_type,
                            byte_size=summary.byte_size,
                            sha256=summary.sha256,
                            manifest_revision=prepared._snapshot.revision + 1,
                            duplicate=DuplicateContentSignal(duplicates),
                        )
                    elif replace_result.state is ManifestReplaceState.UNKNOWN:
                        preserve_source = True
                        preserve_manifest_candidate = True
                        reject_source_ingest(
                            SourceIngestErrorCode.COMMIT_FAILED,
                            "source.commit_uncertain",
                        )
                    else:
                        reject_source_ingest(
                            SourceIngestErrorCode.COMMIT_FAILED,
                            "source.manifest_replace",
                        )
        except BaseException as error:
            failure = error
        finally:
            if source_published and manifest_handle is not None and committed_result is None:
                resolved = _resolve_manifest_destination(
                    prepared._authority,
                    candidate_identity=manifest_handle.info.identity,
                    expected_identity=prepared._snapshot.identity,
                )
                if resolved is ManifestReplaceState.COMMITTED:
                    preserve_source = True
                    preserve_manifest_candidate = True
                    committed_result = SourceImportResult(
                        case_id=summary.case_id,
                        source_id=summary.source_id,
                        page_ids=page_ids,
                        media_type=summary.media_type,
                        byte_size=summary.byte_size,
                        sha256=summary.sha256,
                        manifest_revision=prepared._snapshot.revision + 1,
                        duplicate=DuplicateContentSignal(duplicates),
                    )
                elif resolved is ManifestReplaceState.UNKNOWN:
                    preserve_source = True
                    preserve_manifest_candidate = True
            if manifest_handle is not None:
                if committed_result is None and not preserve_manifest_candidate:
                    _delete_handle_no_raise(manifest_handle)
                else:
                    _close_handle_no_raise(manifest_handle)
            if committed_result is not None:
                prepared._complete(preserve_source=True)
            elif preserve_source:
                prepared._complete(preserve_source=True)
            elif source_published:
                prepared._complete(preserve_source=False)
        if committed_result is not None:
            return committed_result
        if isinstance(failure, SourceIngestError):
            raise failure
        if isinstance(failure, CaseLifecycleError):
            _map_manifest_error(failure)
        if isinstance(failure, WorkspaceError):
            _map_workspace_error(failure, preparing=False)
        if isinstance(failure, Exception):
            reject_source_ingest(SourceIngestErrorCode.COMMIT_FAILED, "source.commit_storage")
        if failure is not None:
            raise failure
        reject_source_ingest(SourceIngestErrorCode.COMMIT_FAILED, "source.commit_result")


class _RetainedManifestSnapshot(AbstractContextManager[tuple[_ManifestSnapshot, WindowsHandle]]):
    __slots__ = ("_handle", "_snapshot")

    def __init__(self, snapshot: _ManifestSnapshot, handle: WindowsHandle) -> None:
        self._snapshot = snapshot
        self._handle = handle

    def __enter__(self) -> tuple[_ManifestSnapshot, WindowsHandle]:
        return self._snapshot, self._handle

    def __exit__(self, *_exc: object) -> None:
        self._handle.close()


def _open_manifest_snapshot(authority: AuthenticatedCase) -> _RetainedManifestSnapshot:
    snapshot, handle = _read_retained_manifest_snapshot(authority)
    return _RetainedManifestSnapshot(snapshot, handle)


def _read_retained_manifest_snapshot(
    authority: AuthenticatedCase,
    *,
    share_delete: bool = False,
    delete: bool = False,
) -> tuple[_ManifestSnapshot, WindowsHandle]:
    path = authority.layout.manifest
    handle: WindowsHandle | None = None
    try:
        handle = open_handle(path, read=True, delete=delete, share_delete=share_delete)
        require_expected_handle(handle, path, directory=False)
        if handle.info.is_sparse or handle.info.link_count != 1:
            _reject_manifest("source.manifest_identity")
        require_no_alternate_streams(path)
        payload = handle.read_bytes(maximum_bytes=MAX_MANIFEST_BYTES)
        snapshot = _validate_ingest_manifest(payload, handle.info.identity, authority.case_id)
        return snapshot, handle
    except BaseException:
        if handle is not None:
            _close_handle_no_raise(handle)
        raise


def _resolve_manifest_destination(
    authority: AuthenticatedCase,
    *,
    candidate_identity: FileIdentity,
    expected_identity: FileIdentity,
) -> ManifestReplaceState:
    """Resolve the manifest name after any interruption at the replacement boundary."""

    try:
        with open_handle(authority.layout.manifest, share_delete=True) as current:
            require_expected_handle(
                current,
                authority.layout.manifest,
                directory=False,
            )
            if current.info.is_reparse_point or current.info.link_count != 1:
                return ManifestReplaceState.UNKNOWN
            require_no_alternate_streams(authority.layout.manifest)
            if current.info.identity == candidate_identity:
                return ManifestReplaceState.COMMITTED
            if current.info.identity == expected_identity:
                return ManifestReplaceState.NOT_COMMITTED
    except Exception:
        return ManifestReplaceState.UNKNOWN
    return ManifestReplaceState.UNKNOWN


def _validate_ingest_manifest(
    payload: bytes,
    identity: FileIdentity,
    expected_case_id: UUID,
) -> _ManifestSnapshot:
    try:
        document = _decode_manifest(payload)
        summary = _summary_from_manifest(document, expected_case_id)
    except CaseLifecycleError:
        raise
    if document["template_ref"] is not None:
        _reject_manifest("source.manifest_template")
    if any(document[key] != [] for key in _EMPTY_IMPORT_COLLECTIONS):
        _reject_manifest("source.manifest_scope")
    if document["constraints"] != _INITIAL_CONSTRAINTS:
        _reject_manifest("source.manifest_constraints")
    if document["export_history"] != _INITIAL_EXPORT_HISTORY:
        _reject_manifest("source.manifest_export_history")

    raw_sources = cast(list[object], document["sources"])
    raw_pages = cast(list[object], document["pages"])
    raw_base = cast(list[object], document["page_sequence_base"])
    raw_current = cast(list[object], document["page_sequence"])
    sources: list[_StoredSource] = []
    identifiers: set[UUID] = {expected_case_id}
    expected_page_pairs: list[tuple[UUID, int]] = []
    previous_imported_at = summary.created_at
    for raw in raw_sources:
        if type(raw) is not dict:
            _reject_manifest("source.manifest_source")
        record = cast(dict[str, object], raw)
        if frozenset(record) != _SOURCE_RECORD_KEYS:
            _reject_manifest("source.manifest_source")
        source_id = _manifest_uuid(record["source_id"])
        if source_id in identifiers:
            _reject_manifest("source.manifest_identifier")
        identifiers.add(source_id)
        display_name = record["display_name"]
        try:
            require_source_display_name(display_name)
        except SourceIngestError:
            _reject_manifest("source.manifest_display_name")
        storage_name = record["storage_name"]
        if type(storage_name) is not str or storage_name != f"{source_id}.bin":
            _reject_manifest("source.manifest_storage_name")
        raw_media_type = record["detected_media_type"]
        if type(raw_media_type) is not str:
            _reject_manifest("source.manifest_media_type")
        try:
            media_type = DetectedMediaType(raw_media_type)
        except ValueError:
            _reject_manifest("source.manifest_media_type")
        byte_size = _manifest_positive_integer(record["byte_size"])
        page_count = _manifest_positive_integer(record["page_count"])
        if (
            page_count > MAX_SOURCE_PAGES
            or len(expected_page_pairs) > MAX_SOURCE_PAGES - page_count
        ):
            _reject_manifest("source.manifest_page_count")
        digest = record["sha256"]
        if type(digest) is not str or _SHA256_RE.fullmatch(digest) is None:
            _reject_manifest("source.manifest_digest")
        signature = record["signature_state"]
        encryption = record["encryption_state"]
        if type(signature) is not str or type(encryption) is not str:
            _reject_manifest("source.manifest_inspection")
        if media_type is DetectedMediaType.PDF:
            valid_states = signature in _PDF_SIGNATURE_STATES and encryption in (
                _PDF_ENCRYPTION_STATES
            )
        else:
            valid_states = (
                signature == SourceSignatureState.NOT_APPLICABLE.value
                and encryption == SourceEncryptionState.NOT_APPLICABLE.value
                and page_count == 1
            )
        if not valid_states or record["import_state"] != "complete":
            _reject_manifest("source.manifest_inspection")
        try:
            imported_at = _parse_utc_timestamp(record["imported_at"])
        except CaseLifecycleError:
            _reject_manifest("source.manifest_imported_at")
        if not previous_imported_at <= imported_at <= summary.updated_at:
            _reject_manifest("source.manifest_imported_at")
        previous_imported_at = imported_at
        sources.append(
            _StoredSource(
                source_id,
                storage_name,
                media_type,
                byte_size,
                page_count,
                digest,
                imported_at,
            )
        )
        expected_page_pairs.extend((source_id, index) for index in range(page_count))

    if len(expected_page_pairs) != len(raw_pages) or len(raw_pages) > MAX_SOURCE_PAGES:
        _reject_manifest("source.manifest_pages")
    page_ids: list[UUID] = []
    actual_page_pairs: list[tuple[UUID, int]] = []
    for raw in raw_pages:
        if type(raw) is not dict:
            _reject_manifest("source.manifest_page")
        record = cast(dict[str, object], raw)
        if frozenset(record) != _PAGE_RECORD_KEYS:
            _reject_manifest("source.manifest_page")
        page_id = _manifest_uuid(record["page_id"])
        source_id = _manifest_uuid(record["source_id"])
        index = record["source_page_index"]
        if type(index) is not int or not 0 <= index <= MAX_SAFE_MANIFEST_INTEGER:
            _reject_manifest("source.manifest_page_index")
        if page_id in identifiers:
            _reject_manifest("source.manifest_identifier")
        identifiers.add(page_id)
        page_ids.append(page_id)
        actual_page_pairs.append((source_id, index))
    if actual_page_pairs != expected_page_pairs:
        _reject_manifest("source.manifest_page_catalogue")
    rendered_page_ids = [str(item) for item in page_ids]
    if raw_base != rendered_page_ids or raw_current != rendered_page_ids:
        _reject_manifest("source.manifest_sequence")
    if summary.revision != len(sources):
        _reject_manifest("source.manifest_revision")
    expected_updated = sources[-1].imported_at if sources else summary.created_at
    if summary.updated_at != expected_updated:
        _reject_manifest("source.manifest_updated_at")
    return _ManifestSnapshot(
        payload,
        identity,
        document,
        summary.revision,
        summary.created_at,
        summary.updated_at,
        tuple(sources),
        tuple(page_ids),
        frozenset(identifiers),
    )


def _candidate_manifest_upper_bound(
    snapshot: _ManifestSnapshot,
    *,
    source_id: UUID,
    display_name: str,
    media_type: DetectedMediaType,
    byte_size: int,
    maximum_page_count: int,
) -> int:
    candidate = copy.deepcopy(snapshot.document)
    sources = cast(list[dict[str, object]], candidate["sources"])
    pages = cast(list[dict[str, object]], candidate["pages"])
    base = cast(list[str], candidate["page_sequence_base"])
    current = cast(list[str], candidate["page_sequence"])
    sources.append(
        {
            "source_id": str(source_id),
            "display_name": display_name,
            "storage_name": f"{source_id}.bin",
            "detected_media_type": media_type.value,
            "byte_size": byte_size,
            "page_count": maximum_page_count,
            "sha256": "0" * 64,
            "signature_state": SourceSignatureState.MARKERS_DETECTED.value,
            "encryption_state": SourceEncryptionState.NOT_APPLICABLE.value,
            "import_state": "complete",
            "imported_at": _CONSERVATIVE_TIMESTAMP,
        }
    )
    new_page_ids = [_PLACEHOLDER_PAGE_ID] * maximum_page_count
    pages.extend(
        {
            "page_id": page_id,
            "source_id": str(source_id),
            "source_page_index": index,
        }
        for index, page_id in enumerate(new_page_ids)
    )
    base.extend(new_page_ids)
    current.extend(new_page_ids)
    candidate["revision"] = MAX_SAFE_MANIFEST_INTEGER
    candidate["updated_at"] = _CONSERVATIVE_TIMESTAMP
    size = len(_serialize_manifest(candidate))
    if size > MAX_MANIFEST_BYTES:
        reject_source_ingest(
            SourceIngestErrorCode.CAPACITY_EXCEEDED,
            "source.manifest_bytes",
        )
    return size


def _build_candidate_manifest(
    snapshot: _ManifestSnapshot,
    *,
    summary: PreparedSourceSummary,
    display_name: str,
    inspection: CompletedSourceInspection,
    page_ids: tuple[UUID, ...],
    timestamp: datetime,
) -> tuple[bytes, tuple[UUID, ...]]:
    if snapshot.revision >= MAX_SAFE_MANIFEST_INTEGER:
        reject_source_ingest(SourceIngestErrorCode.CAPACITY_EXCEEDED, "source.revision")
    if len(snapshot.page_ids) > MAX_SOURCE_PAGES - len(page_ids):
        reject_source_ingest(SourceIngestErrorCode.CAPACITY_EXCEEDED, "source.pages")
    if len(snapshot.sources) >= MAX_MANIFEST_COLLECTION_ITEMS:
        reject_source_ingest(SourceIngestErrorCode.CAPACITY_EXCEEDED, "source.sources")
    try:
        now = timestamp.astimezone(UTC)
    except AttributeError, ValueError, OverflowError:
        reject_source_ingest(SourceIngestErrorCode.COMMIT_FAILED, "source.clock")
    if type(timestamp) is not datetime or timestamp.tzinfo is None or timestamp.utcoffset() is None:
        reject_source_ingest(SourceIngestErrorCode.COMMIT_FAILED, "source.clock")
    committed_at = max(now, snapshot.updated_at)
    try:
        rendered_at = _render_utc_timestamp(committed_at)
    except CaseLifecycleError:
        reject_source_ingest(SourceIngestErrorCode.COMMIT_FAILED, "source.clock")
    candidate = copy.deepcopy(snapshot.document)
    sources = cast(list[dict[str, object]], candidate["sources"])
    pages = cast(list[dict[str, object]], candidate["pages"])
    base = cast(list[str], candidate["page_sequence_base"])
    current = cast(list[str], candidate["page_sequence"])
    sources.append(
        {
            "source_id": str(summary.source_id),
            "display_name": display_name,
            "storage_name": f"{summary.source_id}.bin",
            "detected_media_type": summary.media_type.value,
            "byte_size": summary.byte_size,
            "page_count": inspection.page_count,
            "sha256": summary.sha256,
            "signature_state": inspection.signature_state.value,
            "encryption_state": inspection.encryption_state.value,
            "import_state": "complete",
            "imported_at": rendered_at,
        }
    )
    rendered_pages = [str(page_id) for page_id in page_ids]
    pages.extend(
        {
            "page_id": str(page_id),
            "source_id": str(summary.source_id),
            "source_page_index": index,
        }
        for index, page_id in enumerate(page_ids)
    )
    base.extend(rendered_pages)
    current.extend(rendered_pages)
    candidate["revision"] = snapshot.revision + 1
    candidate["updated_at"] = rendered_at
    payload = _serialize_manifest(candidate)
    if len(payload) > MAX_MANIFEST_BYTES:
        reject_source_ingest(SourceIngestErrorCode.CAPACITY_EXCEEDED, "source.manifest_bytes")
    _validate_ingest_manifest(payload, snapshot.identity, summary.case_id)
    duplicates = tuple(
        sorted(
            (
                source.source_id
                for source in snapshot.sources
                if source.byte_size == summary.byte_size and source.sha256 == summary.sha256
            ),
            key=str,
        )
    )
    return payload, duplicates


def _serialize_manifest(document: dict[str, object]) -> bytes:
    return (
        json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def _copy_and_verify_source(
    source: object,
    pending: WindowsPendingSourceFile,
    *,
    operation_id: UUID,
    byte_size: int,
    cancellation: CancellationProbe,
    progress: ProgressSink,
) -> str:
    source_digest = hashlib.sha256()
    completed = 0
    while completed < byte_size:
        _require_not_cancelled(cancellation, context="source.copy_cancel")
        count = min(MAXIMUM_STREAM_CHUNK_BYTES, byte_size - completed)
        read_at = getattr(source, "read_at", None)
        if not callable(read_at):
            reject_source_ingest(SourceIngestErrorCode.COPY_FAILED, "source.copy_reader")
        chunk = read_at(completed, count)
        if type(chunk) is not bytes or len(chunk) != count:
            reject_source_ingest(SourceIngestErrorCode.SOURCE_CHANGED, "source.copy_read")
        pending.write_chunk(chunk)
        source_digest.update(chunk)
        completed += count
        _report_progress(
            progress,
            SourceImportProgress(
                operation_id,
                SourceProgressPhase.COPYING,
                completed,
                byte_size,
            ),
        )
    pending.seal(expected_size=byte_size)
    _require_not_cancelled(cancellation, context="source.verify_cancel")
    pending.rewind()
    stored_digest = hashlib.sha256()
    completed = 0
    while completed < byte_size:
        _require_not_cancelled(cancellation, context="source.verify_cancel")
        count = min(MAXIMUM_STREAM_CHUNK_BYTES, byte_size - completed)
        stored_digest.update(pending.read_exact(count))
        completed += count
        _report_progress(
            progress,
            SourceImportProgress(
                operation_id,
                SourceProgressPhase.VERIFYING_COPY,
                completed,
                byte_size,
            ),
        )
    pending.require_eof()
    source_hex = source_digest.hexdigest()
    if stored_digest.hexdigest() != source_hex:
        reject_source_ingest(SourceIngestErrorCode.COPY_FAILED, "source.copy_digest")
    return source_hex


def _allocate_page_ids(
    factory: Callable[[], UUID],
    existing: frozenset[UUID] | set[UUID],
    count: int,
) -> tuple[UUID, ...]:
    used = set(existing)
    result: list[UUID] = []
    for _ in range(count):
        result.append(_allocate_identifier(factory, used, context="source.page_id"))
    return tuple(result)


def _allocate_identifier(
    factory: Callable[[], UUID],
    used: set[UUID],
    *,
    context: str,
) -> UUID:
    for _ in range(_MAXIMUM_IDENTIFIER_ATTEMPTS):
        try:
            candidate = require_internal_id(factory(), context=context)
        except SourceIngestError:
            continue
        if candidate not in used:
            used.add(candidate)
            return candidate
    reject_source_ingest(SourceIngestErrorCode.PUBLISH_FAILED, context)


def _manifest_uuid(value: object) -> UUID:
    if type(value) is not str or _UUID_RE.fullmatch(value) is None:
        _reject_manifest("source.manifest_identifier")
    try:
        parsed = UUID(value)
    except ValueError, AttributeError:
        _reject_manifest("source.manifest_identifier")
    if parsed.version != 4 or str(parsed) != value:
        _reject_manifest("source.manifest_identifier")
    return parsed


def _manifest_positive_integer(value: object) -> int:
    if type(value) is not int or not 1 <= value <= MAX_SAFE_MANIFEST_INTEGER:
        _reject_manifest("source.manifest_integer")
    return value


def _require_not_cancelled(cancellation: CancellationProbe, *, context: str) -> None:
    try:
        cancelled = cancellation()
    except SourceIngestError:
        raise
    except Exception:
        reject_source_ingest(SourceIngestErrorCode.CANCELLATION_FAILED, context)
    if type(cancelled) is not bool:
        reject_source_ingest(SourceIngestErrorCode.CANCELLATION_FAILED, context)
    if cancelled:
        reject_source_ingest(SourceIngestErrorCode.CANCELLED, context)


def _report_progress(progress: ProgressSink, value: SourceImportProgress) -> None:
    try:
        progress(value)
    except SourceIngestError:
        raise
    except Exception:
        reject_source_ingest(SourceIngestErrorCode.PROGRESS_FAILED, "source.progress")


def _mismatch(case_id: UUID, source_id: UUID) -> SourceIntegrityResult:
    return SourceIntegrityResult(case_id, source_id, SourceIntegrityStatus.MISMATCH)


def _reject_manifest(context: str) -> Never:
    reject_source_ingest(SourceIngestErrorCode.MANIFEST_INVALID, context)


def _map_precheck_error(error: ImportPrecheckError) -> Never:
    if error.code is ImportPrecheckErrorCode.SOURCE_CHANGED:
        reject_source_ingest(SourceIngestErrorCode.SOURCE_CHANGED, "source.precheck")
    reject_source_ingest(SourceIngestErrorCode.SOURCE_UNAVAILABLE, "source.precheck")


def _map_manifest_error(error: CaseLifecycleError) -> Never:
    if error.code is CaseLifecycleErrorCode.MANIFEST_VERSION_UNSUPPORTED:
        reject_source_ingest(
            SourceIngestErrorCode.MANIFEST_VERSION_UNSUPPORTED,
            "source.manifest_version",
        )
    reject_source_ingest(SourceIngestErrorCode.MANIFEST_INVALID, "source.manifest")


def _map_workspace_error(error: WorkspaceError, *, preparing: bool) -> Never:
    if error.code is WorkspaceErrorCode.QUOTA_EXCEEDED:
        reject_source_ingest(SourceIngestErrorCode.CAPACITY_EXCEEDED, "source.capacity")
    reject_source_ingest(
        SourceIngestErrorCode.PREPARE_FAILED if preparing else SourceIngestErrorCode.COMMIT_FAILED,
        "source.storage",
    )


def _delete_pending_no_raise(pending: WindowsPendingSourceFile) -> None:
    try:
        pending.mark_for_deletion()
    except BaseException:
        pass
    _close_pending_no_raise(pending)


def _close_pending_no_raise(pending: WindowsPendingSourceFile) -> None:
    try:
        pending.close()
    except BaseException:
        pass


def _delete_handle_no_raise(handle: WindowsHandle) -> None:
    try:
        handle.mark_for_deletion()
    except BaseException:
        pass
    _close_handle_no_raise(handle)


def _close_handle_no_raise(handle: WindowsHandle) -> None:
    try:
        handle.close()
    except BaseException:
        pass

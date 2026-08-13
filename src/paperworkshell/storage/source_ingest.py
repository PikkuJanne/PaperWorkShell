"""Authenticated Windows adapter for immutable source ingest and integrity checks."""

from __future__ import annotations

import copy
import hashlib
import hmac
import os
import re
from collections.abc import Callable
from contextlib import AbstractContextManager, ExitStack
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
from paperworkshell.domain.manifest_repository import (
    ManifestRepositoryError,
    ManifestRepositoryErrorCode,
)
from paperworkshell.domain.pdf_export import (
    MAX_PDF_EXPORT_SOURCES,
    PdfExportPage,
    PdfExportPlan,
    PdfExportSource,
    PdfExportSourceEncryptionState,
    PdfExportSourceMediaType,
    PdfExportSourceSignatureState,
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
    WindowsHandle,
    WindowsPendingSourceFile,
    create_pending_source_file,
    open_handle,
    publish_new_original,
    require_expected_handle,
    require_no_alternate_streams,
    verify_private_acl,
)
from paperworkshell.storage.errors import WorkspaceError, WorkspaceErrorCode
from paperworkshell.storage.manifest_codec import (
    MAX_MANIFEST_BYTES,
    MAX_MANIFEST_COLLECTION_ITEMS,
)
from paperworkshell.storage.manifest_codec import (
    decode_manifest as _decode_manifest,
)
from paperworkshell.storage.manifest_codec import (
    parse_utc_timestamp as _parse_utc_timestamp,
)
from paperworkshell.storage.manifest_codec import (
    render_utc_timestamp as _render_utc_timestamp,
)
from paperworkshell.storage.manifest_codec import (
    summary_from_manifest as _summary_from_manifest,
)
from paperworkshell.storage.manifest_repository import (
    WindowsManifestRepository,
    _ManifestTransactionInterruption,
    canonical_manifest_bytes,
    commit_manifest_transaction,
    validate_manifest_payload,
)
from paperworkshell.storage.prepared_source_registry import (
    PreparedSourceReadLease,
    PreparedSourceReadRegistry,
    PreparedSourceRegistration,
)
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
DeadlineProbe = Callable[[], bool]
ProgressSink = Callable[[SourceImportProgress], None]


@dataclass(frozen=True, slots=True)
class _StoredSource:
    source_id: UUID
    storage_name: str
    media_type: DetectedMediaType
    byte_size: int
    page_count: int
    sha256: str = field(repr=False)
    signature_state: SourceSignatureState
    encryption_state: SourceEncryptionState
    import_state: str
    imported_at: datetime


@dataclass(frozen=True, slots=True)
class _ManifestSnapshot:
    payload: bytes = field(repr=False)
    identity: FileIdentity
    document: dict[str, object] = field(repr=False)
    revision: int
    sha256: str = field(repr=False)
    cas_token: str = field(repr=False)
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
        "_journal_reservation",
        "_journal_stage_path",
        "_manifest_reservation",
        "_manifest_stage_path",
        "_pending",
        "_registration",
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
        journal_reservation: WorkspaceCapacityReservation,
        manifest_stage_path: Path,
        journal_stage_path: Path,
        registration: PreparedSourceRegistration | None = None,
    ) -> None:
        self._store = store
        self._authority = authority
        self._summary = summary
        self._display_name = display_name
        self._snapshot = snapshot
        self._pending = pending
        self._registration = registration
        self._source_reservation = source_reservation
        self._manifest_reservation = manifest_reservation
        self._journal_reservation = journal_reservation
        self._manifest_stage_path = manifest_stage_path
        self._journal_stage_path = journal_stage_path
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
        deadline: DeadlineProbe,
        progress: ProgressSink,
    ) -> SourceImportResult:
        """Publish the exact source first and replace the complete manifest last."""

        if self._closed:
            reject_source_ingest(SourceIngestErrorCode.COMMIT_FAILED, "source.commit_closed")
        try:
            return self._store._commit_prepared(
                self,
                inspection,
                cancellation=cancellation,
                deadline=deadline,
                progress=progress,
            )
        except BaseException:
            self.abort()
            raise

    def abort(self) -> None:
        """Remove only the exact retained pending object, then release all authority."""

        if self._closed:
            return
        self._closed = True
        self._revoke_registration()
        _delete_pending_no_raise(self._pending)
        self._release()

    def _complete(self, *, preserve_source: bool) -> None:
        if self._closed:
            return
        self._closed = True
        self._revoke_registration()
        if preserve_source:
            _close_pending_no_raise(self._pending)
        else:
            _delete_pending_no_raise(self._pending)
        self._release()

    def _revoke_registration(self) -> None:
        if self._registration is not None:
            try:
                self._registration.close()
            except BaseException:
                pass
            self._registration = None

    def _release(self) -> None:
        try:
            self._journal_reservation.release()
        except BaseException:
            pass
        try:
            self._manifest_reservation.release()
        except BaseException:
            pass
        try:
            self._source_reservation.release()
        except BaseException:
            pass
        try:
            self._authority.close()
        except BaseException:
            pass


class CommittedSourcePageReadLease(AbstractContextManager["CommittedSourcePageReadLease"]):
    """Retain one manifest-bound immutable source page through an exact read handle."""

    __slots__ = (
        "_case_id",
        "_closed",
        "_encryption_state",
        "_page_id",
        "_signature_state",
        "_source_media_type",
        "_source_byte_size",
        "_source_handle",
        "_source_id",
        "_source_page_count",
        "_source_page_index",
        "_source_sha256",
        "_stack",
    )

    def __init__(
        self,
        *,
        stack: ExitStack,
        source_handle: WindowsHandle,
        case_id: UUID,
        source_id: UUID,
        source_media_type: DetectedMediaType,
        page_id: UUID,
        source_page_index: int,
        source_page_count: int,
        source_byte_size: int,
        source_sha256: str,
        signature_state: SourceSignatureState,
        encryption_state: SourceEncryptionState,
    ) -> None:
        self._stack = stack
        self._source_handle = source_handle
        self._case_id = case_id
        self._source_id = source_id
        self._source_media_type = source_media_type
        self._page_id = page_id
        self._source_page_index = source_page_index
        self._source_page_count = source_page_count
        self._source_byte_size = source_byte_size
        self._source_sha256 = source_sha256
        self._signature_state = signature_state
        self._encryption_state = encryption_state
        self._closed = False

    def __repr__(self) -> str:
        label = (
            "CommittedPdfPageReadLease"
            if self._source_media_type is DetectedMediaType.PDF
            else "CommittedImagePageReadLease"
        )
        return f"{label}(<redacted>)"

    def __enter__(self) -> Self:
        if self._closed:
            reject_source_ingest(SourceIngestErrorCode.SOURCE_UNAVAILABLE, "source.page_lease")
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @property
    def handle(self) -> WindowsHandle:
        """Return the exact read-only source handle retained by this lease."""

        if self._closed:
            reject_source_ingest(SourceIngestErrorCode.SOURCE_UNAVAILABLE, "source.page_lease")
        previous = self._source_handle.info
        try:
            current = self._source_handle.refresh_info()
            if (
                current.identity != previous.identity
                or current.logical_size != self._source_byte_size
                or current.is_directory
                or current.is_reparse_point
                or current.is_sparse
                or current.link_count != 1
            ):
                reject_source_ingest(SourceIngestErrorCode.SOURCE_CHANGED, "source.page_handle")
            require_no_alternate_streams(current.final_path)
        except SourceIngestError:
            raise
        except WorkspaceError:
            reject_source_ingest(SourceIngestErrorCode.SOURCE_CHANGED, "source.page_handle")
        return self._source_handle

    @property
    def case_id(self) -> UUID:
        return self._case_id

    @property
    def source_id(self) -> UUID:
        return self._source_id

    @property
    def source_media_type(self) -> DetectedMediaType:
        return self._source_media_type

    @property
    def page_id(self) -> UUID:
        return self._page_id

    @property
    def source_page_index(self) -> int:
        return self._source_page_index

    @property
    def source_page_count(self) -> int:
        return self._source_page_count

    @property
    def source_byte_size(self) -> int:
        return self._source_byte_size

    @property
    def source_sha256(self) -> str:
        return self._source_sha256

    @property
    def signature_state(self) -> SourceSignatureState:
        """Return the signature-marker state authenticated from the retained manifest."""

        return self._signature_state

    @property
    def encryption_state(self) -> SourceEncryptionState:
        """Return the encryption state authenticated from the retained manifest."""

        return self._encryption_state

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stack.close()

    @property
    def handle_value(self) -> int:
        """Return the authenticated source handle solely for reduced-right duplication."""

        value = self.handle.value
        if type(value) is not int or value <= 0:
            reject_source_ingest(SourceIngestErrorCode.SOURCE_UNAVAILABLE, "source.page_handle")
        return value

    def revalidate(
        self,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> None:
        """Re-hash the exact retained source after the one-shot worker has terminated."""

        if self._closed:
            reject_source_ingest(SourceIngestErrorCode.SOURCE_UNAVAILABLE, "source.page_lease")
        _require_active(
            cancellation,
            deadline,
            cancel_context="source.page_cancel",
            timeout_context="source.page_timeout",
        )
        previous = self.handle.info
        self.handle.rewind()
        digest = hashlib.sha256()
        completed = 0
        try:
            while completed < self._source_byte_size:
                _require_active(
                    cancellation,
                    deadline,
                    cancel_context="source.page_cancel",
                    timeout_context="source.page_timeout",
                )
                count = min(MAXIMUM_STREAM_CHUNK_BYTES, self._source_byte_size - completed)
                digest.update(self.handle.read_exact(count))
                completed += count
            current = self.handle.require_eof()
        except SourceIngestError:
            raise
        except BaseException:
            reject_source_ingest(SourceIngestErrorCode.SOURCE_CHANGED, "source.page_revalidate")
        finally:
            try:
                self.handle.rewind()
            except BaseException:
                pass
        if (
            current.identity != previous.identity
            or current.logical_size != self._source_byte_size
            or not hmac.compare_digest(digest.hexdigest(), self._source_sha256)
        ):
            reject_source_ingest(SourceIngestErrorCode.SOURCE_CHANGED, "source.page_revalidate")


# Retain the released PDF-facing name while image intake consumes the canonical
# source-page lease structurally through its narrower worker port.
CommittedPdfPageReadLease = CommittedSourcePageReadLease


class CommittedPdfSourceReadLease:
    """One member of a manifest-bound batch, exposed without a path or raw object."""

    __slots__ = (
        "_close_batch",
        "_expected_identity",
        "_handle",
        "_source",
    )

    def __init__(
        self,
        *,
        source: PdfExportSource,
        handle: WindowsHandle,
        close_batch: Callable[[], None],
    ) -> None:
        self._source = source
        self._handle = handle
        self._expected_identity = handle.info.identity
        self._close_batch = close_batch

    def __repr__(self) -> str:
        return "CommittedPdfSourceReadLease(<redacted>)"

    @property
    def source_id(self) -> UUID:
        return self._source.source_id

    @property
    def media_type(self) -> PdfExportSourceMediaType:
        return self._source.media_type

    @property
    def byte_size(self) -> int:
        return self._source.byte_size

    @property
    def page_count(self) -> int:
        return self._source.page_count

    @property
    def sha256(self) -> str:
        return self._source.sha256

    @property
    def signature_state(self) -> PdfExportSourceSignatureState:
        return self._source.signature_state

    @property
    def encryption_state(self) -> PdfExportSourceEncryptionState:
        return self._source.encryption_state

    @property
    def handle_value(self) -> int:
        """Return a currently authenticated value solely for worker duplication."""

        try:
            _require_retained_pdf_source_metadata(
                self._handle,
                expected_identity=self._expected_identity,
                expected_size=self._source.byte_size,
            )
            value = self._handle.value
            if type(value) is not int or value <= 0:
                reject_source_ingest(
                    SourceIngestErrorCode.SOURCE_UNAVAILABLE,
                    "source.batch_handle",
                )
            return value
        except SourceIngestError:
            _close_batch_no_raise(self._close_batch)
            raise
        except BaseException:
            _close_batch_no_raise(self._close_batch)
            reject_source_ingest(
                SourceIngestErrorCode.SOURCE_CHANGED,
                "source.batch_handle",
            )

    def _revalidate(
        self,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> None:
        _hash_retained_pdf_source(
            self._handle,
            self._source,
            expected_identity=self._expected_identity,
            deadline=deadline,
            cancellation=cancellation,
        )


class CommittedPdfSourceBatchReadLease(AbstractContextManager["CommittedPdfSourceBatchReadLease"]):
    """Own one retained manifest and all exact first-use-ordered export sources."""

    __slots__ = (
        "_case_id",
        "_closed",
        "_manifest_cas_token",
        "_manifest_revision",
        "_manifest_sha256",
        "_sources",
        "_stack",
    )

    def __init__(
        self,
        *,
        stack: ExitStack,
        case_id: UUID,
        manifest_revision: int,
        manifest_sha256: str,
        manifest_cas_token: str,
        retained_sources: tuple[tuple[PdfExportSource, WindowsHandle], ...],
    ) -> None:
        self._stack = stack
        self._case_id = case_id
        self._manifest_revision = manifest_revision
        self._manifest_sha256 = manifest_sha256
        self._manifest_cas_token = manifest_cas_token
        self._closed = False
        self._sources = tuple(
            CommittedPdfSourceReadLease(
                source=source,
                handle=handle,
                close_batch=self.close,
            )
            for source, handle in retained_sources
        )

    def __repr__(self) -> str:
        return "CommittedPdfSourceBatchReadLease(<redacted>)"

    def __enter__(self) -> Self:
        if self._closed:
            reject_source_ingest(
                SourceIngestErrorCode.SOURCE_UNAVAILABLE,
                "source.batch_lease",
            )
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @property
    def case_id(self) -> UUID:
        return self._case_id

    @property
    def manifest_revision(self) -> int:
        return self._manifest_revision

    @property
    def manifest_sha256(self) -> str:
        return self._manifest_sha256

    @property
    def manifest_cas_token(self) -> str:
        return self._manifest_cas_token

    @property
    def sources(self) -> tuple[CommittedPdfSourceReadLease, ...]:
        return self._sources

    def revalidate(
        self,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> None:
        """Re-hash the same retained sources after the worker has terminated."""

        if self._closed:
            reject_source_ingest(
                SourceIngestErrorCode.SOURCE_UNAVAILABLE,
                "source.batch_lease",
            )
        try:
            _require_active(
                cancellation,
                deadline,
                cancel_context="source.batch_cancel",
                timeout_context="source.batch_timeout",
            )
            for source in self._sources:
                source._revalidate(deadline=deadline, cancellation=cancellation)
        except SourceIngestError:
            _close_batch_no_raise(self.close)
            raise
        except BaseException:
            _close_batch_no_raise(self.close)
            reject_source_ingest(
                SourceIngestErrorCode.SOURCE_CHANGED,
                "source.batch_revalidate",
            )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stack.close()


class WindowsSourceIngestStore:
    """Prepare, atomically commit, and later verify immutable case sources."""

    __slots__ = (
        "_clock",
        "_id_factory",
        "_manager",
        "_maximum_page_count",
        "_prechecker",
        "_prepared_registry",
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
        prepared_registry: PreparedSourceReadRegistry | None = None,
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
        self._prepared_registry = prepared_registry or PreparedSourceReadRegistry()

    def borrow_prepared_source(self, summary: PreparedSourceSummary) -> PreparedSourceReadLease:
        """Borrow the exact sealed pending source through its complete path-free summary."""

        return self._prepared_registry.borrow(summary)

    def borrow_committed_pdf_page(
        self,
        case_id: UUID,
        page_id: UUID,
        *,
        cancellation: CancellationProbe,
        deadline: DeadlineProbe,
    ) -> CommittedPdfPageReadLease:
        """Retain a current manifest mapping and integrity-checked PDF source handle."""

        return self._borrow_committed_page(
            case_id,
            page_id,
            allowed_media_types=frozenset({DetectedMediaType.PDF}),
            cancellation=cancellation,
            deadline=deadline,
        )

    def borrow_committed_source_page(
        self,
        case_id: UUID,
        page_id: UUID,
        *,
        cancellation: CancellationProbe,
        deadline: DeadlineProbe,
    ) -> CommittedSourcePageReadLease:
        """Retain one manifest-authenticated PDF, JPEG, or PNG source page for OCR."""

        return self._borrow_committed_page(
            case_id,
            page_id,
            allowed_media_types=frozenset(
                {
                    DetectedMediaType.PDF,
                    DetectedMediaType.JPEG,
                    DetectedMediaType.PNG,
                }
            ),
            cancellation=cancellation,
            deadline=deadline,
        )

    def borrow_committed_image_page(
        self,
        case_id: UUID,
        page_id: UUID,
        *,
        cancellation: CancellationProbe,
        deadline: DeadlineProbe,
    ) -> CommittedSourcePageReadLease:
        """Retain one immutable JPEG/PNG page and its exact read-only source handle."""

        return self._borrow_committed_page(
            case_id,
            page_id,
            allowed_media_types=frozenset({DetectedMediaType.JPEG, DetectedMediaType.PNG}),
            cancellation=cancellation,
            deadline=deadline,
        )

    def _borrow_committed_page(
        self,
        case_id: UUID,
        page_id: UUID,
        *,
        allowed_media_types: frozenset[DetectedMediaType],
        cancellation: CancellationProbe,
        deadline: DeadlineProbe,
    ) -> CommittedSourcePageReadLease:
        """Authenticate, rehash, and retain one permitted source-page mapping."""

        selected_case = require_internal_id(case_id, context="source.page_case")
        selected_page = require_internal_id(page_id, context="source.page_id")
        if selected_case == selected_page:
            reject_source_ingest(SourceIngestErrorCode.MODEL_INVALID, "source.page_ids")
        _require_active(
            cancellation,
            deadline,
            cancel_context="source.page_cancel",
            timeout_context="source.page_timeout",
        )

        def coordination_checkpoint() -> None:
            _require_active(
                cancellation,
                deadline,
                cancel_context="source.page_cancel",
                timeout_context="source.page_timeout",
            )

        stack = ExitStack()
        borrowed = False
        try:
            authority = stack.enter_context(
                self._manager.authenticate_case(
                    selected_case,
                    retain_lease=True,
                    coordination_checkpoint=coordination_checkpoint,
                )
            )
            snapshot, manifest_handle = _read_retained_manifest_snapshot(authority)
            stack.enter_context(manifest_handle)
            record, source_page_index = _resolve_source_page(
                snapshot,
                selected_page,
                allowed_media_types=allowed_media_types,
            )

            originals_root = stack.enter_context(open_handle(authority.layout.originals))
            require_expected_handle(
                originals_root,
                authority.layout.originals,
                directory=True,
            )
            verify_private_acl(originals_root)
            require_no_alternate_streams(authority.layout.originals)

            source_path = authority.layout.originals / record.storage_name
            source_handle = stack.enter_context(open_handle(source_path, read=True))
            require_expected_handle(source_handle, source_path, directory=False)
            info = source_handle.info
            if info.is_sparse or info.link_count != 1 or info.logical_size != record.byte_size:
                reject_source_ingest(SourceIngestErrorCode.SOURCE_CHANGED, "source.page_identity")
            require_no_alternate_streams(source_path)
            source_handle.rewind()
            digest = hashlib.sha256()
            completed = 0
            while completed < record.byte_size:
                _require_active(
                    cancellation,
                    deadline,
                    cancel_context="source.page_cancel",
                    timeout_context="source.page_timeout",
                )
                count = min(MAXIMUM_STREAM_CHUNK_BYTES, record.byte_size - completed)
                digest.update(source_handle.read_exact(count))
                completed += count
            _require_active(
                cancellation,
                deadline,
                cancel_context="source.page_cancel",
                timeout_context="source.page_timeout",
            )
            current = source_handle.require_eof()
            if (
                current.identity != info.identity
                or current.logical_size != record.byte_size
                or digest.hexdigest() != record.sha256
            ):
                reject_source_ingest(SourceIngestErrorCode.SOURCE_CHANGED, "source.page_integrity")
            source_handle.rewind()

            lease = CommittedSourcePageReadLease(
                stack=stack,
                source_handle=source_handle,
                case_id=selected_case,
                source_id=record.source_id,
                source_media_type=record.media_type,
                page_id=selected_page,
                source_page_index=source_page_index,
                source_page_count=record.page_count,
                source_byte_size=record.byte_size,
                source_sha256=record.sha256,
                signature_state=record.signature_state,
                encryption_state=record.encryption_state,
            )
            borrowed = True
            return lease
        except SourceIngestError:
            raise
        except CaseLifecycleError as error:
            _map_manifest_error(error)
        except WorkspaceError:
            reject_source_ingest(SourceIngestErrorCode.SOURCE_UNAVAILABLE, "source.page_storage")
        except Exception:
            reject_source_ingest(SourceIngestErrorCode.SOURCE_UNAVAILABLE, "source.page_storage")
        finally:
            if not borrowed:
                stack.close()

    def borrow_committed_pdf_sources(
        self,
        plan: PdfExportPlan,
        *,
        cancellation: CancellationProbe,
        deadline: DeadlineProbe,
    ) -> CommittedPdfSourceBatchReadLease:
        """Retain every exact PDF source bound to one authenticated export plan."""

        selected_plan = _require_committed_pdf_export_plan(plan)
        selected_case = selected_plan.case_id
        _require_active(
            cancellation,
            deadline,
            cancel_context="source.batch_cancel",
            timeout_context="source.batch_timeout",
        )

        def coordination_checkpoint() -> None:
            _require_active(
                cancellation,
                deadline,
                cancel_context="source.batch_cancel",
                timeout_context="source.batch_timeout",
            )

        stack = ExitStack()
        borrowed = False
        try:
            authority = stack.enter_context(
                self._manager.authenticate_case(
                    selected_case,
                    retain_lease=True,
                    coordination_checkpoint=coordination_checkpoint,
                )
            )
            snapshot, manifest_handle = _read_retained_manifest_snapshot(authority)
            stack.enter_context(manifest_handle)
            if (
                snapshot.revision != selected_plan.manifest_revision
                or snapshot.sha256 != selected_plan.manifest_sha256
                or snapshot.cas_token != selected_plan.manifest_cas_token
            ):
                reject_source_ingest(
                    SourceIngestErrorCode.MANIFEST_CONFLICT,
                    "source.batch_manifest",
                )

            records_by_id = {record.source_id: record for record in snapshot.sources}
            if len(records_by_id) != len(snapshot.sources):
                reject_source_ingest(
                    SourceIngestErrorCode.MANIFEST_INVALID,
                    "source.batch_catalogue",
                )
            selected_records: list[_StoredSource] = []
            for source in selected_plan.sources:
                record = records_by_id.get(source.source_id)
                if record is None:
                    reject_source_ingest(
                        SourceIngestErrorCode.SOURCE_UNAVAILABLE,
                        "source.batch_missing",
                    )
                _require_matching_pdf_export_source(record, source)
                selected_records.append(record)

            originals_root = stack.enter_context(open_handle(authority.layout.originals))
            require_expected_handle(
                originals_root,
                authority.layout.originals,
                directory=True,
            )
            verify_private_acl(originals_root)
            require_no_alternate_streams(authority.layout.originals)

            retained_sources: list[tuple[PdfExportSource, WindowsHandle]] = []
            for source, record in zip(
                selected_plan.sources,
                selected_records,
                strict=True,
            ):
                _require_active(
                    cancellation,
                    deadline,
                    cancel_context="source.batch_cancel",
                    timeout_context="source.batch_timeout",
                )
                source_path = authority.layout.originals / record.storage_name
                source_handle = stack.enter_context(open_handle(source_path, read=True))
                require_expected_handle(source_handle, source_path, directory=False)
                _hash_retained_pdf_source(
                    source_handle,
                    source,
                    expected_identity=source_handle.info.identity,
                    cancellation=cancellation,
                    deadline=deadline,
                )
                retained_sources.append((source, source_handle))

            lease = CommittedPdfSourceBatchReadLease(
                stack=stack,
                case_id=selected_case,
                manifest_revision=snapshot.revision,
                manifest_sha256=snapshot.sha256,
                manifest_cas_token=snapshot.cas_token,
                retained_sources=tuple(retained_sources),
            )
            borrowed = True
            return lease
        except SourceIngestError:
            raise
        except CaseLifecycleError as error:
            _map_manifest_error(error)
        except WorkspaceError:
            reject_source_ingest(
                SourceIngestErrorCode.SOURCE_UNAVAILABLE,
                "source.batch_storage",
            )
        except BaseException:
            reject_source_ingest(
                SourceIngestErrorCode.SOURCE_UNAVAILABLE,
                "source.batch_storage",
            )
        finally:
            if not borrowed:
                stack.close()

    def prepare(
        self,
        case_id: UUID,
        source_path: Path,
        display_name: str,
        *,
        cancellation: CancellationProbe,
        deadline: DeadlineProbe,
        progress: ProgressSink,
    ) -> WindowsPreparedSourceLease:
        """Copy through the one live precheck handle and return after terminal recheck."""

        selected_case = require_internal_id(case_id, context="source.prepare_case")
        safe_display_name = require_source_display_name(display_name)
        if not isinstance(source_path, Path):
            reject_source_ingest(SourceIngestErrorCode.SOURCE_PATH_INVALID, "source.prepare_path")
        _require_active(
            cancellation,
            deadline,
            cancel_context="source.prepare_cancel",
            timeout_context="source.prepare_timeout",
        )

        def coordination_checkpoint() -> None:
            _require_active(
                cancellation,
                deadline,
                cancel_context="source.prepare_cancel",
                timeout_context="source.prepare_timeout",
            )

        # Resolve a retained journal checkpoint before copying from a new external
        # source. The transaction itself repeats the pending-checkpoint check while
        # holding the cross-process case mutex, closing the race after this load.
        try:
            WindowsManifestRepository(self._manager).load(
                selected_case,
                coordination_checkpoint=coordination_checkpoint,
            )
        except ManifestRepositoryError as error:
            _map_repository_error(error)

        authority: AuthenticatedCase | None = None
        pending: WindowsPendingSourceFile | None = None
        source_reservation: WorkspaceCapacityReservation | None = None
        manifest_reservation: WorkspaceCapacityReservation | None = None
        journal_reservation: WorkspaceCapacityReservation | None = None
        prepared = False
        registration: PreparedSourceRegistration | None = None
        try:
            authority = self._manager.authenticate_case(
                selected_case,
                retain_lease=True,
                coordination_checkpoint=coordination_checkpoint,
            )
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
                journal_stage_id = _allocate_identifier(
                    self._temp_id_factory,
                    used_ids,
                    context="source.journal_stage_id",
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
                journal_stage_path = authority.layout.temp / f"{journal_stage_id}.tmp"
                source_reservation = self._manager.reserve_capacity(
                    selected_case,
                    QuotaArea.TEMP,
                    relative_path=f"temp/{pending_id}.tmp",
                    is_directory=False,
                    byte_count=result.byte_size,
                    coordination_checkpoint=coordination_checkpoint,
                )
                manifest_reservation = self._manager.reserve_capacity(
                    selected_case,
                    QuotaArea.TEMP,
                    relative_path=f"temp/{manifest_stage_id}.tmp",
                    is_directory=False,
                    byte_count=manifest_upper_bound,
                    coordination_checkpoint=coordination_checkpoint,
                )
                journal_reservation = self._manager.reserve_capacity(
                    selected_case,
                    QuotaArea.TEMP,
                    relative_path=f"temp/{journal_stage_id}.tmp",
                    is_directory=False,
                    byte_count=manifest_upper_bound,
                    coordination_checkpoint=coordination_checkpoint,
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
                        coordination_checkpoint=coordination_checkpoint,
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
                            deadline=deadline,
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
                registration = self._prepared_registry.register(summary, pending)
            prepared = True
            if (
                authority is None
                or pending is None
                or source_reservation is None
                or manifest_reservation is None
                or journal_reservation is None
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
                journal_reservation=journal_reservation,
                manifest_stage_path=manifest_stage_path,
                journal_stage_path=journal_stage_path,
                registration=registration,
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
                if registration is not None:
                    registration.close()
                if pending is not None:
                    _delete_pending_no_raise(pending)
                if manifest_reservation is not None:
                    manifest_reservation.release()
                if journal_reservation is not None:
                    journal_reservation.release()
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
        deadline: DeadlineProbe,
        progress: ProgressSink,
    ) -> SourceIntegrityResult:
        """Stream one committed source through an authenticated read-only handle."""

        selected_case = require_internal_id(case_id, context="integrity.case_id")
        selected_source = require_internal_id(source_id, context="integrity.source_id")
        _require_active(
            cancellation,
            deadline,
            cancel_context="integrity.cancel",
            timeout_context="integrity.timeout",
        )

        def coordination_checkpoint() -> None:
            _require_active(
                cancellation,
                deadline,
                cancel_context="integrity.cancel",
                timeout_context="integrity.timeout",
            )

        try:
            WindowsManifestRepository(self._manager).load(
                selected_case,
                coordination_checkpoint=coordination_checkpoint,
            )
            with self._manager.authenticate_case(
                selected_case,
                coordination_checkpoint=coordination_checkpoint,
            ) as authority:
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
                                _require_active(
                                    cancellation,
                                    deadline,
                                    cancel_context="integrity.cancel",
                                    timeout_context="integrity.timeout",
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
                            _require_active(
                                cancellation,
                                deadline,
                                cancel_context="integrity.cancel",
                                timeout_context="integrity.timeout",
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
        except ManifestRepositoryError as error:
            _map_repository_error(error)
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
        deadline: DeadlineProbe,
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
        _require_active(
            cancellation,
            deadline,
            cancel_context="source.commit_cancel",
            timeout_context="source.commit_timeout",
        )

        def coordination_checkpoint() -> None:
            _require_active(
                cancellation,
                deadline,
                cancel_context="source.commit_cancel",
                timeout_context="source.commit_timeout",
            )

        page_ids = _allocate_page_ids(
            self._id_factory,
            prepared._snapshot.identifiers | {summary.source_id},
            inspection.page_count,
        )
        used_ids = set(prepared._snapshot.identifiers)
        used_ids.update(page_ids)
        used_ids.add(summary.source_id)
        raw_operations = prepared._snapshot.document["page_operations"]
        if type(raw_operations) is not list:
            reject_source_ingest(SourceIngestErrorCode.COMMIT_FAILED, "source.operations")
        import_operation_id = (
            _allocate_identifier(
                self._id_factory,
                used_ids,
                context="source.import_operation_id",
            )
            if raw_operations
            else None
        )
        candidate, duplicates = _build_candidate_manifest(
            prepared._snapshot,
            summary=summary,
            display_name=prepared._display_name,
            inspection=inspection,
            page_ids=page_ids,
            import_operation_id=import_operation_id,
            timestamp=self._clock(),
        )
        if (
            len(candidate) > prepared._manifest_reservation.byte_count
            or len(candidate) > prepared._journal_reservation.byte_count
        ):
            reject_source_ingest(
                SourceIngestErrorCode.CAPACITY_EXCEEDED,
                "source.manifest_reservation",
            )
        _require_active(
            cancellation,
            deadline,
            cancel_context="source.commit_cancel",
            timeout_context="source.commit_timeout",
        )
        _report_progress(
            progress,
            SourceImportProgress(
                summary.pending_id,
                SourceProgressPhase.COMMITTING,
                0,
                1,
            ),
        )

        source_published = False

        def publish_source() -> None:
            nonlocal source_published
            authority = prepared._authority
            with prepared._source_reservation.publication_guard(
                case_identity=authority.identity,
                relative_parts=prepared._source_reservation.relative_parts,
                is_directory=False,
                byte_count=summary.byte_size,
                entry_count=1,
                coordination_checkpoint=coordination_checkpoint,
            ):
                with open_handle(authority.layout.originals) as originals_root:
                    require_expected_handle(
                        originals_root,
                        authority.layout.originals,
                        directory=True,
                    )
                    verify_private_acl(originals_root)
                    require_no_alternate_streams(authority.layout.originals)
                    require_expected_handle(
                        prepared._pending.handle,
                        authority.layout.temp / f"{summary.pending_id}.tmp",
                        directory=False,
                    )
                    publish_new_original(
                        prepared._pending,
                        originals_root,
                        storage_name=f"{summary.source_id}.bin",
                    )
                    source_published = True

        preserve_source = False
        failure: BaseException | None = None
        committed_revision: int | None = None
        try:
            saved = commit_manifest_transaction(
                authority=prepared._authority,
                expected_payload=prepared._snapshot.payload,
                expected_sha256=hashlib.sha256(prepared._snapshot.payload).hexdigest(),
                expected_cas_token=None,
                candidate_payload=candidate,
                manifest_stage_path=prepared._manifest_stage_path,
                journal_stage_path=prepared._journal_stage_path,
                manifest_reservation=prepared._manifest_reservation,
                journal_reservation=prepared._journal_reservation,
                before_checkpoint=publish_source,
                coordination_checkpoint=coordination_checkpoint,
            )
            committed_revision = saved.revision
            preserve_source = True
        except _ManifestTransactionInterruption as interruption:
            failure = interruption.cause
            preserve_source = source_published and interruption.preserve_recovery
        except BaseException as error:
            failure = error
            if isinstance(error, ManifestRepositoryError):
                preserve_source = source_published and error.context in {
                    "manifest.checkpoint_uncertain",
                    "manifest.commit_uncertain",
                    "manifest.commit_replace",
                    "manifest.transaction_verify",
                }
            elif source_published:
                # A process-control interruption has no safe user-facing settlement. Once
                # publication occurred, preserve the exact source so a committed or uncertain
                # checkpoint can recover without leaving a manifest that names missing bytes.
                preserve_source = True
        finally:
            prepared._complete(preserve_source=preserve_source)

        if committed_revision is not None:
            _report_progress(
                progress,
                SourceImportProgress(
                    summary.pending_id,
                    SourceProgressPhase.COMMITTING,
                    1,
                    1,
                ),
            )
            return SourceImportResult(
                case_id=summary.case_id,
                source_id=summary.source_id,
                page_ids=page_ids,
                media_type=summary.media_type,
                byte_size=summary.byte_size,
                sha256=summary.sha256,
                manifest_revision=committed_revision,
                duplicate=DuplicateContentSignal(duplicates),
            )
        if isinstance(failure, SourceIngestError):
            raise failure
        if isinstance(failure, ManifestRepositoryError):
            _map_repository_error(failure)
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


def _require_committed_pdf_export_plan(value: object) -> PdfExportPlan:
    """Defensively recheck the path-free plan at the untrusted storage boundary."""

    if type(value) is not PdfExportPlan:
        reject_source_ingest(SourceIngestErrorCode.MODEL_INVALID, "source.batch_plan")
    try:
        plan = value
        require_internal_id(plan.case_id, context="source.batch_case")
        if (
            type(plan.manifest_revision) is not int
            or not 0 <= plan.manifest_revision <= MAX_SAFE_MANIFEST_INTEGER
            or type(plan.manifest_sha256) is not str
            or _SHA256_RE.fullmatch(plan.manifest_sha256) is None
            or type(plan.manifest_cas_token) is not str
            or not 16 <= len(plan.manifest_cas_token) <= 128
            or any(
                not (character.isascii() and (character.isalnum() or character in "_-"))
                for character in plan.manifest_cas_token
            )
            or type(plan.sources) is not tuple
            or not 1 <= len(plan.sources) <= MAX_PDF_EXPORT_SOURCES
            or type(plan.pages) is not tuple
            or not plan.pages
        ):
            reject_source_ingest(SourceIngestErrorCode.MODEL_INVALID, "source.batch_plan")
        for source in plan.sources:
            if (
                type(source) is not PdfExportSource
                or type(source.source_id) is not UUID
                or source.source_id.version != 4
                or type(source.byte_size) is not int
                or not 1 <= source.byte_size <= MAX_SAFE_MANIFEST_INTEGER
                or type(source.page_count) is not int
                or not 1 <= source.page_count <= MAX_SOURCE_PAGES
                or type(source.sha256) is not str
                or _SHA256_RE.fullmatch(source.sha256) is None
                or type(source.signature_state) is not PdfExportSourceSignatureState
                or source.media_type is not PdfExportSourceMediaType.PDF
                or source.encryption_state is not PdfExportSourceEncryptionState.UNENCRYPTED
            ):
                reject_source_ingest(SourceIngestErrorCode.MODEL_INVALID, "source.batch_source")
        if any(type(page) is not PdfExportPage for page in plan.pages):
            reject_source_ingest(SourceIngestErrorCode.MODEL_INVALID, "source.batch_pages")
        source_ids = tuple(source.source_id for source in plan.sources)
        first_use_order = tuple(dict.fromkeys(page.source_id for page in plan.pages))
        if len(set(source_ids)) != len(source_ids) or source_ids != first_use_order:
            reject_source_ingest(
                SourceIngestErrorCode.MODEL_INVALID,
                "source.batch_source_order",
            )
    except SourceIngestError:
        raise
    except BaseException:
        reject_source_ingest(SourceIngestErrorCode.MODEL_INVALID, "source.batch_plan")
    return plan


def _require_matching_pdf_export_source(
    record: _StoredSource,
    source: PdfExportSource,
) -> None:
    if record.import_state != "complete":
        reject_source_ingest(
            SourceIngestErrorCode.SOURCE_UNAVAILABLE,
            "source.batch_not_ready",
        )
    if record.media_type is not DetectedMediaType.PDF:
        reject_source_ingest(
            SourceIngestErrorCode.SOURCE_UNAVAILABLE,
            "source.batch_media",
        )
    if record.encryption_state is not SourceEncryptionState.UNENCRYPTED:
        reject_source_ingest(
            SourceIngestErrorCode.SOURCE_UNAVAILABLE,
            "source.batch_encryption",
        )
    try:
        signature_state = PdfExportSourceSignatureState(record.signature_state.value)
    except ValueError:
        reject_source_ingest(
            SourceIngestErrorCode.SOURCE_CHANGED,
            "source.batch_signature",
        )
    if (
        record.source_id != source.source_id
        or record.byte_size != source.byte_size
        or record.page_count != source.page_count
        or record.sha256 != source.sha256
        or signature_state is not source.signature_state
    ):
        reject_source_ingest(
            SourceIngestErrorCode.SOURCE_CHANGED,
            "source.batch_facts",
        )


def _require_retained_pdf_source_metadata(
    handle: WindowsHandle,
    *,
    expected_identity: FileIdentity,
    expected_size: int,
) -> FileIdentity:
    try:
        current = handle.refresh_info()
        if (
            current.identity != expected_identity
            or current.logical_size != expected_size
            or current.is_directory
            or current.is_reparse_point
            or current.is_sparse
            or current.link_count != 1
        ):
            reject_source_ingest(
                SourceIngestErrorCode.SOURCE_CHANGED,
                "source.batch_identity",
            )
        require_no_alternate_streams(current.final_path)
        return current.identity
    except SourceIngestError:
        raise
    except BaseException:
        reject_source_ingest(
            SourceIngestErrorCode.SOURCE_CHANGED,
            "source.batch_identity",
        )


def _hash_retained_pdf_source(
    handle: WindowsHandle,
    source: PdfExportSource,
    *,
    expected_identity: FileIdentity,
    cancellation: CancellationProbe,
    deadline: DeadlineProbe,
) -> None:
    try:
        _require_active(
            cancellation,
            deadline,
            cancel_context="source.batch_cancel",
            timeout_context="source.batch_timeout",
        )
        _require_retained_pdf_source_metadata(
            handle,
            expected_identity=expected_identity,
            expected_size=source.byte_size,
        )
        handle.rewind()
        digest = hashlib.sha256()
        completed = 0
        while completed < source.byte_size:
            _require_active(
                cancellation,
                deadline,
                cancel_context="source.batch_cancel",
                timeout_context="source.batch_timeout",
            )
            count = min(MAXIMUM_STREAM_CHUNK_BYTES, source.byte_size - completed)
            digest.update(handle.read_exact(count))
            completed += count
        _require_active(
            cancellation,
            deadline,
            cancel_context="source.batch_cancel",
            timeout_context="source.batch_timeout",
        )
        current = handle.require_eof()
        if (
            current.identity != expected_identity
            or current.logical_size != source.byte_size
            or current.is_directory
            or current.is_reparse_point
            or current.is_sparse
            or current.link_count != 1
            or digest.hexdigest() != source.sha256
        ):
            reject_source_ingest(
                SourceIngestErrorCode.SOURCE_CHANGED,
                "source.batch_integrity",
            )
        require_no_alternate_streams(current.final_path)
        handle.rewind()
    except SourceIngestError:
        raise
    except BaseException:
        reject_source_ingest(
            SourceIngestErrorCode.SOURCE_CHANGED,
            "source.batch_integrity",
        )


def _manifest_cas_token(identity: FileIdentity, digest: str) -> str:
    material = (
        identity.volume_serial_number.to_bytes(8, byteorder="big", signed=False)
        + identity.file_id
        + bytes.fromhex(digest)
    )
    return hashlib.sha256(material).hexdigest()


def _close_batch_no_raise(close: Callable[[], None]) -> None:
    try:
        close()
    except BaseException:
        pass


def _resolve_source_page(
    snapshot: _ManifestSnapshot,
    page_id: UUID,
    *,
    allowed_media_types: frozenset[DetectedMediaType],
) -> tuple[_StoredSource, int]:
    """Resolve one page through the strict current manifest without exposing display data."""

    try:
        catalogue_index = snapshot.page_ids.index(page_id)
    except ValueError:
        reject_source_ingest(SourceIngestErrorCode.SOURCE_UNAVAILABLE, "source.page_missing")
    first_page = 0
    for source in snapshot.sources:
        next_page = first_page + source.page_count
        if catalogue_index < next_page:
            if source.media_type not in allowed_media_types:
                reject_source_ingest(SourceIngestErrorCode.SOURCE_UNAVAILABLE, "source.page_media")
            return source, catalogue_index - first_page
        first_page = next_page
    reject_source_ingest(SourceIngestErrorCode.MANIFEST_INVALID, "source.page_catalogue")


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


def _validate_ingest_manifest(
    payload: bytes,
    identity: FileIdentity,
    expected_case_id: UUID,
) -> _ManifestSnapshot:
    try:
        validate_manifest_payload(payload, expected_case_id)
        document = _decode_manifest(payload)
        summary = _summary_from_manifest(document, expected_case_id)
    except CaseLifecycleError:
        raise
    except ManifestRepositoryError as error:
        _map_repository_error(error)

    raw_sources = cast(list[object], document["sources"])
    raw_pages = cast(list[object], document["pages"])
    sources: list[_StoredSource] = []
    identifiers = _manifest_identifiers(document, expected_case_id)
    expected_page_pairs: list[tuple[UUID, int]] = []
    for raw in raw_sources:
        if type(raw) is not dict:
            _reject_manifest("source.manifest_source")
        record = cast(dict[str, object], raw)
        if frozenset(record) != _SOURCE_RECORD_KEYS:
            _reject_manifest("source.manifest_source")
        source_id = _manifest_uuid(record["source_id"])
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
        try:
            signature_state = SourceSignatureState(signature)
            encryption_state = SourceEncryptionState(encryption)
        except ValueError:
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
        sources.append(
            _StoredSource(
                source_id,
                storage_name,
                media_type,
                byte_size,
                page_count,
                digest,
                signature_state,
                encryption_state,
                "complete",
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
        page_ids.append(page_id)
        actual_page_pairs.append((source_id, index))
    if actual_page_pairs != expected_page_pairs:
        _reject_manifest("source.manifest_page_catalogue")
    manifest_sha256 = hashlib.sha256(payload).hexdigest()
    return _ManifestSnapshot(
        payload,
        identity,
        document,
        summary.revision,
        manifest_sha256,
        _manifest_cas_token(identity, manifest_sha256),
        summary.created_at,
        summary.updated_at,
        tuple(sources),
        tuple(page_ids),
        frozenset(identifiers),
    )


def _manifest_identifiers(
    document: dict[str, object],
    expected_case_id: UUID,
) -> set[UUID]:
    """Collect every identity-defining UUID from one fully validated manifest."""

    identifiers = {expected_case_id}
    for collection_name, field_name in (
        ("sources", "source_id"),
        ("pages", "page_id"),
        ("page_operations", "operation_id"),
        ("assets", "asset_id"),
        ("overlays", "overlay_id"),
        ("assignments", "assignment_id"),
        ("confirmations", "confirmation_id"),
    ):
        collection = cast(list[dict[str, object]], document[collection_name])
        identifiers.update(_manifest_uuid(record[field_name]) for record in collection)
    operations = cast(list[dict[str, object]], document["page_operations"])
    for operation in operations:
        payload = cast(dict[str, object], operation["payload"])
        if payload["kind"] == "set_group":
            identifiers.add(_manifest_uuid(payload["group_id"]))
    export_history = cast(dict[str, object], document["export_history"])
    entries = cast(list[dict[str, object]], export_history["entries"])
    identifiers.update(_manifest_uuid(record["export_id"]) for record in entries)
    return identifiers


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
    operations = cast(list[dict[str, object]], candidate["page_operations"])
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
    if operations:
        if len(operations) >= MAX_MANIFEST_COLLECTION_ITEMS:
            reject_source_ingest(
                SourceIngestErrorCode.CAPACITY_EXCEEDED,
                "source.manifest_operations",
            )
        before = list(current)
        current.extend(new_page_ids)
        operations.append(
            {
                "operation_id": _PLACEHOLDER_PAGE_ID,
                "operation_version": 1,
                "sequence": len(operations) + 1,
                "payload": {
                    "kind": "change_sequence",
                    "reason": "import",
                    "before_page_ids": before,
                    "after_page_ids": list(current),
                },
            }
        )
    else:
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
    import_operation_id: UUID | None = None,
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
    operations = cast(list[dict[str, object]], candidate["page_operations"])
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
    if operations:
        if import_operation_id is None or len(operations) >= MAX_MANIFEST_COLLECTION_ITEMS:
            reject_source_ingest(
                SourceIngestErrorCode.CAPACITY_EXCEEDED,
                "source.manifest_operations",
            )
        before = list(current)
        current.extend(rendered_pages)
        operations.append(
            {
                "operation_id": str(import_operation_id),
                "operation_version": 1,
                "sequence": len(operations) + 1,
                "payload": {
                    "kind": "change_sequence",
                    "reason": "import",
                    "before_page_ids": before,
                    "after_page_ids": list(current),
                },
            }
        )
    else:
        if import_operation_id is not None:
            reject_source_ingest(SourceIngestErrorCode.COMMIT_FAILED, "source.operations")
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
    try:
        return canonical_manifest_bytes(document)
    except ManifestRepositoryError as error:
        _map_repository_error(error)


def _copy_and_verify_source(
    source: object,
    pending: WindowsPendingSourceFile,
    *,
    operation_id: UUID,
    byte_size: int,
    cancellation: CancellationProbe,
    deadline: DeadlineProbe,
    progress: ProgressSink,
) -> str:
    source_digest = hashlib.sha256()
    completed = 0
    while completed < byte_size:
        _require_active(
            cancellation,
            deadline,
            cancel_context="source.copy_cancel",
            timeout_context="source.copy_timeout",
        )
        count = min(MAXIMUM_STREAM_CHUNK_BYTES, byte_size - completed)
        read_at = getattr(source, "read_at", None)
        if not callable(read_at):
            reject_source_ingest(SourceIngestErrorCode.COPY_FAILED, "source.copy_reader")
        chunk = read_at(completed, count)
        if type(chunk) is not bytes or len(chunk) != count:
            reject_source_ingest(SourceIngestErrorCode.SOURCE_CHANGED, "source.copy_read")
        _require_active(
            cancellation,
            deadline,
            cancel_context="source.copy_cancel",
            timeout_context="source.copy_timeout",
        )
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
    _require_active(
        cancellation,
        deadline,
        cancel_context="source.copy_cancel",
        timeout_context="source.copy_timeout",
    )
    pending.seal(expected_size=byte_size)
    _require_active(
        cancellation,
        deadline,
        cancel_context="source.verify_cancel",
        timeout_context="source.verify_timeout",
    )
    pending.rewind()
    stored_digest = hashlib.sha256()
    completed = 0
    while completed < byte_size:
        _require_active(
            cancellation,
            deadline,
            cancel_context="source.verify_cancel",
            timeout_context="source.verify_timeout",
        )
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
    _require_active(
        cancellation,
        deadline,
        cancel_context="source.verify_cancel",
        timeout_context="source.verify_timeout",
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


def _require_not_expired(deadline: DeadlineProbe, *, context: str) -> None:
    try:
        expired = deadline()
    except SourceIngestError:
        raise
    except Exception:
        reject_source_ingest(SourceIngestErrorCode.INTERNAL_ERROR, context)
    if type(expired) is not bool:
        reject_source_ingest(SourceIngestErrorCode.INTERNAL_ERROR, context)
    if expired:
        reject_source_ingest(SourceIngestErrorCode.TIMED_OUT, context)


def _require_active(
    cancellation: CancellationProbe,
    deadline: DeadlineProbe,
    *,
    cancel_context: str,
    timeout_context: str,
) -> None:
    _require_not_expired(deadline, context=timeout_context)
    _require_not_cancelled(cancellation, context=cancel_context)


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


def _map_repository_error(error: ManifestRepositoryError) -> Never:
    if error.code is ManifestRepositoryErrorCode.VERSION_UNSUPPORTED:
        reject_source_ingest(
            SourceIngestErrorCode.MANIFEST_VERSION_UNSUPPORTED,
            "source.manifest_version",
        )
    if error.code is ManifestRepositoryErrorCode.REVISION_CONFLICT:
        reject_source_ingest(SourceIngestErrorCode.MANIFEST_CONFLICT, "source.manifest_cas")
    if error.code is ManifestRepositoryErrorCode.CAPACITY_EXCEEDED:
        reject_source_ingest(SourceIngestErrorCode.CAPACITY_EXCEEDED, "source.capacity")
    if error.code in {
        ManifestRepositoryErrorCode.MANIFEST_INVALID,
        ManifestRepositoryErrorCode.CASE_MISMATCH,
    }:
        reject_source_ingest(SourceIngestErrorCode.MANIFEST_INVALID, "source.manifest")
    reject_source_ingest(SourceIngestErrorCode.COMMIT_FAILED, "source.commit_storage")


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

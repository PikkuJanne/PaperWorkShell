"""Authenticated Windows storage for immutable signature-image case assets."""

from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import Callable
from contextlib import AbstractContextManager, ExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Never, Self, cast
from uuid import UUID, uuid4

from paperworkshell.domain.case_lifecycle import MAX_SAFE_MANIFEST_INTEGER, CaseLifecycleError
from paperworkshell.domain.import_precheck import (
    DetectedMediaType,
    ImportPrecheckError,
    ImportPrecheckErrorCode,
)
from paperworkshell.domain.manifest_repository import (
    ManifestRepositoryError,
    ManifestRepositoryErrorCode,
    ManifestSnapshot,
)
from paperworkshell.domain.signature_images import (
    PreparedSignatureImageSummary,
    SignatureImageCleanupResult,
    SignatureImageCleanupStatus,
    SignatureImageError,
    SignatureImageErrorCode,
    SignatureImageMediaType,
    SignatureImagePlacementCommitResult,
    SignatureImageProgress,
    SignatureImageProgressPhase,
    reject_signature_image,
)
from paperworkshell.overlays.models import (
    OverlayAssetKind,
    OverlayAssetMediaType,
    OverlayAssetRecord,
    OverlayCollection,
    SignatureImageOverlayPayload,
)
from paperworkshell.overlays.signature_commands import (
    SignatureAssetCleanupDisposition,
    SignatureImageManifestChange,
)
from paperworkshell.storage._windows import (
    MAXIMUM_STREAM_CHUNK_BYTES,
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
from paperworkshell.storage.manifest_codec import parse_utc_timestamp, render_utc_timestamp
from paperworkshell.storage.manifest_repository import (
    WindowsManifestRepository,
    _ManifestTransactionInterruption,
    _read_checkpoint,
    canonical_manifest_bytes,
    commit_manifest_transaction,
)
from paperworkshell.storage.quotas import QuotaArea
from paperworkshell.storage.source_ingest import _read_retained_manifest_snapshot
from paperworkshell.storage.source_precheck import WindowsSourcePrechecker
from paperworkshell.storage.workspace import (
    AuthenticatedCase,
    CaseWorkspaceManager,
    WorkspaceCapacityReservation,
)

_MAXIMUM_IDENTIFIER_ATTEMPTS = 32
_MANIFEST_GROWTH_SAFETY_BYTES = 512
_MANIFEST_RECOVERY_REQUIRED_CONTEXTS = frozenset(
    {
        "manifest.checkpoint_uncertain",
        "manifest.commit_uncertain",
        "manifest.commit_replace",
        "manifest.transaction_verify",
    }
)
_PRECHECK_LIMIT_ERRORS = frozenset(
    {
        ImportPrecheckErrorCode.LIMIT_INVALID,
        ImportPrecheckErrorCode.SOURCE_TOO_LARGE,
        ImportPrecheckErrorCode.HEADER_LIMIT_EXCEEDED,
        ImportPrecheckErrorCode.IMAGE_DIMENSION_LIMIT_EXCEEDED,
        ImportPrecheckErrorCode.IMAGE_PIXEL_LIMIT_EXCEEDED,
        ImportPrecheckErrorCode.DECOMPRESSION_LIMIT_EXCEEDED,
        ImportPrecheckErrorCode.PAGE_LIMIT_EXCEEDED,
    }
)
_PRECHECK_MALFORMED_ERRORS = frozenset(
    {
        ImportPrecheckErrorCode.SOURCE_EMPTY,
        ImportPrecheckErrorCode.HEADER_TRUNCATED,
        ImportPrecheckErrorCode.HEADER_MALFORMED,
        ImportPrecheckErrorCode.IMAGE_DIMENSIONS_UNAVAILABLE,
    }
)

CancellationProbe = Callable[[], bool]
DeadlineProbe = Callable[[], bool]
ProgressSink = Callable[[SignatureImageProgress], None]


@dataclass(frozen=True, slots=True, repr=False)
class _PreparedSnapshot:
    snapshot: ManifestSnapshot = field(repr=False)
    updated_at: datetime

    def __post_init__(self) -> None:
        if type(self.snapshot) is not ManifestSnapshot or type(self.updated_at) is not datetime:
            reject_signature_image(SignatureImageErrorCode.INTERNAL_ERROR, "signature.snapshot")


class WindowsPreparedSignatureImageLease(
    AbstractContextManager["WindowsPreparedSignatureImageLease"]
):
    """Retain one sealed exact copy through preview and source-first commit."""

    __slots__ = (
        "_authority",
        "_closed",
        "_pending",
        "_snapshot",
        "_source_reservation",
        "_store",
        "_summary",
    )

    def __init__(
        self,
        *,
        store: WindowsSignatureImageAssetStore,
        authority: AuthenticatedCase,
        summary: PreparedSignatureImageSummary,
        snapshot: _PreparedSnapshot,
        pending: WindowsPendingSourceFile,
        source_reservation: WorkspaceCapacityReservation,
    ) -> None:
        self._store = store
        self._authority = authority
        self._summary = summary
        self._snapshot = snapshot
        self._pending = pending
        self._source_reservation = source_reservation
        self._closed = False

    @property
    def summary(self) -> PreparedSignatureImageSummary:
        return self._summary

    @property
    def case_id(self) -> UUID:
        return self._summary.case_id

    @property
    def asset_id(self) -> UUID:
        return self._summary.asset_id

    @property
    def media_type(self) -> SignatureImageMediaType:
        return self._summary.media_type

    @property
    def byte_size(self) -> int:
        return self._summary.byte_size

    @property
    def sha256(self) -> str:
        return self._summary.sha256

    @property
    def handle_value(self) -> int:
        if self._closed:
            reject_signature_image(SignatureImageErrorCode.ASSET_CHANGED, "signature.closed")
        return self._pending.handle.value

    @property
    def manifest_snapshot(self) -> ManifestSnapshot:
        return self._snapshot.snapshot

    def __enter__(self) -> Self:
        if self._closed:
            reject_signature_image(SignatureImageErrorCode.ASSET_CHANGED, "signature.closed")
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
        change: SignatureImageManifestChange,
        *,
        cancellation: CancellationProbe,
        deadline: DeadlineProbe,
        progress: ProgressSink,
    ) -> SignatureImagePlacementCommitResult:
        if self._closed:
            reject_signature_image(SignatureImageErrorCode.COMMIT_FAILED, "signature.closed")
        try:
            return self._store._commit_prepared(
                self,
                change,
                cancellation=cancellation,
                deadline=deadline,
                progress=progress,
            )
        except BaseException:
            self.abort()
            raise

    def abort(self) -> None:
        if self._closed:
            return
        self._closed = True
        _delete_pending_no_raise(self._pending)
        self._release()

    def _complete(self, *, preserve_published: bool) -> None:
        if self._closed:
            return
        self._closed = True
        if preserve_published:
            _close_pending_no_raise(self._pending)
        else:
            _delete_pending_no_raise(self._pending)
        self._release()

    def _release(self) -> None:
        try:
            self._source_reservation.release()
        except BaseException:
            pass
        try:
            self._authority.close()
        except BaseException:
            pass

    def __repr__(self) -> str:
        return f"WindowsPreparedSignatureImageLease(closed={self._closed!r})"


class CommittedSignatureImageReadLease(AbstractContextManager["CommittedSignatureImageReadLease"]):
    """Retain one exact manifest-authenticated asset read handle."""

    __slots__ = ("_asset", "_case_id", "_closed", "_handle", "_stack")

    def __init__(
        self,
        *,
        stack: ExitStack,
        handle: WindowsHandle,
        case_id: UUID,
        asset: OverlayAssetRecord,
    ) -> None:
        self._stack = stack
        self._handle = handle
        self._case_id = case_id
        self._asset = asset
        self._closed = False

    @property
    def case_id(self) -> UUID:
        return self._case_id

    @property
    def asset_id(self) -> UUID:
        return self._asset.asset_id

    @property
    def media_type(self) -> SignatureImageMediaType:
        return _signature_media_type(self._asset.media_type)

    @property
    def byte_size(self) -> int:
        return self._asset.byte_size

    @property
    def sha256(self) -> str:
        return self._asset.sha256

    @property
    def handle_value(self) -> int:
        if self._closed:
            reject_signature_image(SignatureImageErrorCode.ASSET_CHANGED, "signature.read_closed")
        return self._handle.value

    def __enter__(self) -> Self:
        if self._closed:
            reject_signature_image(SignatureImageErrorCode.ASSET_CHANGED, "signature.read_closed")
        return self

    def __exit__(self, *_exception: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stack.close()

    def __repr__(self) -> str:
        return f"CommittedSignatureImageReadLease(closed={self._closed!r})"


class WindowsSignatureImageAssetStore:
    """Stage, commit, borrow, and clean immutable signature-image assets."""

    __slots__ = ("_clock", "_id_factory", "_manager", "_prechecker", "_temp_id_factory")

    def __init__(
        self,
        manager: CaseWorkspaceManager,
        prechecker: WindowsSourcePrechecker,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], UUID] = uuid4,
        temp_id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if (
            not isinstance(manager, CaseWorkspaceManager)
            or not isinstance(prechecker, WindowsSourcePrechecker)
            or (clock is not None and not callable(clock))
            or not callable(id_factory)
            or not callable(temp_id_factory)
        ):
            reject_signature_image(SignatureImageErrorCode.MODEL_INVALID, "signature.store")
        self._manager = manager
        self._prechecker = prechecker
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory
        self._temp_id_factory = temp_id_factory

    def prepare(
        self,
        case_id: UUID,
        source_path: Path,
        *,
        cancellation: CancellationProbe,
        deadline: DeadlineProbe,
        progress: ProgressSink,
    ) -> WindowsPreparedSignatureImageLease:
        """Copy and rehash one pinned JPEG/PNG while retaining no external name."""

        selected_case = _require_uuid(case_id, "signature.prepare_case")
        if not isinstance(source_path, Path):
            reject_signature_image(SignatureImageErrorCode.SOURCE_PATH_INVALID, "signature.path")
        _require_active(cancellation, deadline)

        def checkpoint() -> None:
            _require_active(cancellation, deadline)

        try:
            loaded = WindowsManifestRepository(self._manager).load(
                selected_case,
                coordination_checkpoint=checkpoint,
            )
        except ManifestRepositoryError as error:
            _map_manifest_error(error)
        snapshot = loaded.snapshot
        identifiers = _manifest_identifiers(snapshot.document)
        identifiers.add(selected_case)
        pending_id = _allocate_identifier(self._temp_id_factory, identifiers)
        identifiers.add(pending_id)
        asset_id = _allocate_identifier(self._id_factory, identifiers)

        authority: AuthenticatedCase | None = None
        pending: WindowsPendingSourceFile | None = None
        reservation: WorkspaceCapacityReservation | None = None
        prepared = False
        try:
            authority = self._manager.authenticate_case(
                selected_case,
                retain_lease=True,
                coordination_checkpoint=checkpoint,
            )
            with self._prechecker.open(source_path) as source:
                result = source.result
                if result.media_type not in {DetectedMediaType.JPEG, DetectedMediaType.PNG}:
                    reject_signature_image(
                        SignatureImageErrorCode.MEDIA_UNSUPPORTED,
                        "signature.media",
                    )
                collection = OverlayCollection.from_manifest_document(snapshot.document)
                if len(collection.assets) >= 4_096:
                    reject_signature_image(
                        SignatureImageErrorCode.CAPACITY_EXCEEDED,
                        "signature.assets",
                    )
                stage_path = authority.layout.temp / f"{pending_id}.tmp"
                reservation = self._manager.reserve_capacity(
                    selected_case,
                    QuotaArea.TEMP,
                    relative_path=f"temp/{pending_id}.tmp",
                    is_directory=False,
                    byte_count=result.byte_size,
                    coordination_checkpoint=checkpoint,
                )
                with open_handle(authority.layout.temp) as temp_root:
                    require_expected_handle(temp_root, authority.layout.temp, directory=True)
                    verify_private_acl(temp_root)
                    require_no_alternate_streams(authority.layout.temp)
                    with reservation.publication_guard(
                        case_identity=authority.identity,
                        relative_parts=("temp", f"{pending_id}.tmp"),
                        is_directory=False,
                        byte_count=result.byte_size,
                        entry_count=1,
                        coordination_checkpoint=checkpoint,
                    ):
                        pending = create_pending_source_file(stage_path, temp_root=temp_root)
                        digest = _copy_and_verify(
                            source,
                            pending,
                            operation_id=pending_id,
                            byte_size=result.byte_size,
                            cancellation=cancellation,
                            deadline=deadline,
                            progress=progress,
                        )
                summary = PreparedSignatureImageSummary(
                    pending_id,
                    selected_case,
                    asset_id,
                    _detected_media_type(result.media_type),
                    result.byte_size,
                    digest,
                )
            prepared = True
            if authority is None or pending is None or reservation is None:
                reject_signature_image(SignatureImageErrorCode.INTERNAL_ERROR, "signature.prepare")
            updated_at = parse_utc_timestamp(snapshot.document.get("updated_at"))
            return WindowsPreparedSignatureImageLease(
                store=self,
                authority=authority,
                summary=summary,
                snapshot=_PreparedSnapshot(snapshot, updated_at),
                pending=pending,
                source_reservation=reservation,
            )
        except SignatureImageError:
            raise
        except ImportPrecheckError as error:
            _map_precheck_error(error)
        except WorkspaceError as error:
            _map_workspace_error(error, preparing=True)
        except CaseLifecycleError:
            reject_signature_image(SignatureImageErrorCode.CASE_CHANGED, "signature.case")
        except Exception:
            reject_signature_image(SignatureImageErrorCode.COPY_FAILED, "signature.prepare")
        finally:
            if not prepared:
                if pending is not None:
                    _delete_pending_no_raise(pending)
                if reservation is not None:
                    reservation.release()
                if authority is not None:
                    try:
                        authority.close()
                    except BaseException:
                        pass

    def borrow_committed_signature_image(
        self,
        case_id: UUID,
        asset_id: UUID,
        *,
        cancellation: CancellationProbe,
        deadline: DeadlineProbe,
    ) -> CommittedSignatureImageReadLease:
        """Rehash and retain one exact protected asset for reduced-handle transfer."""

        selected_case = _require_uuid(case_id, "signature.read_case")
        selected_asset = _require_uuid(asset_id, "signature.read_asset")
        _require_active(cancellation, deadline)
        stack = ExitStack()
        borrowed = False
        try:
            authority = stack.enter_context(
                self._manager.authenticate_case(selected_case, retain_lease=True)
            )
            raw_snapshot, manifest_handle = _read_retained_manifest_snapshot(authority)
            stack.enter_context(manifest_handle)
            collection = OverlayCollection.from_manifest_document(raw_snapshot.document)
            asset = next(
                (
                    candidate
                    for candidate in collection.assets
                    if candidate.asset_id == selected_asset
                ),
                None,
            )
            if asset is None:
                reject_signature_image(SignatureImageErrorCode.ASSET_UNKNOWN, "signature.asset")
            originals_root = stack.enter_context(open_handle(authority.layout.originals))
            require_expected_handle(originals_root, authority.layout.originals, directory=True)
            verify_private_acl(originals_root)
            require_no_alternate_streams(authority.layout.originals)
            asset_path = authority.layout.originals / asset.storage_name
            handle = stack.enter_context(open_handle(asset_path, read=True))
            require_expected_handle(originals_root, authority.layout.originals, directory=True)
            require_expected_handle(handle, asset_path, directory=False)
            _authenticate_asset_handle(
                handle,
                asset,
                cancellation=cancellation,
                deadline=deadline,
            )
            require_expected_handle(originals_root, authority.layout.originals, directory=True)
            require_expected_handle(handle, asset_path, directory=False)
            handle.rewind()
            lease = CommittedSignatureImageReadLease(
                stack=stack,
                handle=handle,
                case_id=selected_case,
                asset=asset,
            )
            borrowed = True
            return lease
        except SignatureImageError:
            raise
        except WorkspaceError:
            reject_signature_image(SignatureImageErrorCode.ASSET_CHANGED, "signature.storage")
        except CaseLifecycleError:
            reject_signature_image(SignatureImageErrorCode.CASE_CHANGED, "signature.case")
        except Exception:
            reject_signature_image(SignatureImageErrorCode.ASSET_CHANGED, "signature.storage")
        finally:
            if not borrowed:
                stack.close()

    def cleanup_unused_asset(
        self,
        case_id: UUID,
        asset: OverlayAssetRecord,
        *,
        cancellation: CancellationProbe,
        deadline: DeadlineProbe,
        progress: ProgressSink,
    ) -> SignatureImageCleanupResult:
        """Boundedly authenticate and remove a file the manifest no longer names."""

        selected_case = _require_uuid(case_id, "signature.cleanup_case")
        if type(asset) is not OverlayAssetRecord:
            reject_signature_image(SignatureImageErrorCode.MODEL_INVALID, "signature.cleanup_asset")
        _require_active(cancellation, deadline)

        def coordination_checkpoint() -> None:
            _require_active(cancellation, deadline)

        try:
            resolved = (
                WindowsManifestRepository(self._manager)
                .load(
                    selected_case,
                    coordination_checkpoint=coordination_checkpoint,
                )
                .snapshot
            )
            with self._manager.authenticate_case(selected_case) as authority:
                with authority.transaction_guard():
                    _require_active(cancellation, deadline)
                    snapshot, manifest_handle = _read_retained_manifest_snapshot(authority)
                    with manifest_handle:
                        checkpoint = _read_checkpoint(authority)
                        if (
                            snapshot.sha256 != resolved.sha256
                            or snapshot.cas_token != resolved.cas_token
                            or (
                                checkpoint is not None
                                and (
                                    checkpoint.revision > snapshot.revision
                                    or checkpoint.sha256 != snapshot.sha256
                                )
                            )
                        ):
                            reject_signature_image(
                                SignatureImageErrorCode.CASE_CHANGED,
                                "signature.cleanup_manifest",
                            )
                        identifiers = _manifest_identifiers(snapshot.document)
                        identifiers.update({selected_case, asset.asset_id})
                        operation_id = _allocate_identifier(self._temp_id_factory, identifiers)
                        total_units = asset.byte_size + 1
                        _report(
                            progress,
                            operation_id,
                            SignatureImageProgressPhase.CLEANING_UP,
                            0,
                            total_units,
                        )
                        collection = OverlayCollection.from_manifest_document(snapshot.document)
                        if _manifest_references_original(
                            snapshot.document,
                            asset.storage_name,
                        ):
                            result = SignatureImageCleanupResult(
                                selected_case,
                                asset.asset_id,
                                SignatureImageCleanupStatus.RETAINED,
                            )
                            _report_best_effort(
                                progress,
                                operation_id,
                                SignatureImageProgressPhase.CLEANING_UP,
                                total_units,
                                total_units,
                            )
                            return result
                        if any(
                            type(record.payload) is SignatureImageOverlayPayload
                            and record.payload.asset_id == asset.asset_id
                            for record in collection.overlays
                        ):
                            reject_signature_image(
                                SignatureImageErrorCode.ASSET_REFERENCED,
                                "signature.cleanup_reference",
                            )
                        asset_path = authority.layout.originals / asset.storage_name
                        if not os.path.lexists(asset_path):
                            result = SignatureImageCleanupResult(
                                selected_case,
                                asset.asset_id,
                                SignatureImageCleanupStatus.ALREADY_ABSENT,
                            )
                            _report_best_effort(
                                progress,
                                operation_id,
                                SignatureImageProgressPhase.CLEANING_UP,
                                total_units,
                                total_units,
                            )
                            return result
                        with open_handle(authority.layout.originals) as originals_root:
                            require_expected_handle(
                                originals_root,
                                authority.layout.originals,
                                directory=True,
                            )
                            verify_private_acl(originals_root)
                            require_no_alternate_streams(authority.layout.originals)
                            with open_handle(
                                asset_path,
                                read=True,
                                delete=True,
                            ) as handle:
                                require_expected_handle(
                                    originals_root,
                                    authority.layout.originals,
                                    directory=True,
                                )
                                require_expected_handle(handle, asset_path, directory=False)
                                _authenticate_asset_handle(
                                    handle,
                                    asset,
                                    cancellation=cancellation,
                                    deadline=deadline,
                                    hash_progress=lambda completed: _report(
                                        progress,
                                        operation_id,
                                        SignatureImageProgressPhase.CLEANING_UP,
                                        completed,
                                        total_units,
                                    ),
                                )
                                require_expected_handle(
                                    originals_root,
                                    authority.layout.originals,
                                    directory=True,
                                )
                                require_expected_handle(handle, asset_path, directory=False)
                                _require_active(cancellation, deadline)
                                handle.mark_for_deletion()
                result = SignatureImageCleanupResult(
                    selected_case,
                    asset.asset_id,
                    SignatureImageCleanupStatus.REMOVED,
                )
                _report_best_effort(
                    progress,
                    operation_id,
                    SignatureImageProgressPhase.CLEANING_UP,
                    total_units,
                    total_units,
                )
                return result
        except SignatureImageError:
            raise
        except Exception:
            reject_signature_image(SignatureImageErrorCode.CLEANUP_FAILED, "signature.cleanup")

    def _commit_prepared(
        self,
        prepared: WindowsPreparedSignatureImageLease,
        change: SignatureImageManifestChange,
        *,
        cancellation: CancellationProbe,
        deadline: DeadlineProbe,
        progress: ProgressSink,
    ) -> SignatureImagePlacementCommitResult:
        summary = prepared._summary
        snapshot = prepared._snapshot.snapshot
        if (
            type(change) is not SignatureImageManifestChange
            or change.case_id != summary.case_id
            or change.source_revision != snapshot.revision
        ):
            reject_signature_image(SignatureImageErrorCode.CASE_CHANGED, "signature.change")
        candidate = change.candidate_document
        asset, overlay_id = _require_prepared_candidate(
            candidate,
            summary,
            source_document=snapshot.document,
            cleanup_disposition=change.cleanup_intent.disposition,
        )
        _require_active(cancellation, deadline)
        if snapshot.revision >= MAX_SAFE_MANIFEST_INTEGER:
            reject_signature_image(SignatureImageErrorCode.CAPACITY_EXCEEDED, "signature.revision")
        now = _require_clock(self._clock)
        candidate["revision"] = snapshot.revision + 1
        candidate["updated_at"] = render_utc_timestamp(max(now, prepared._snapshot.updated_at))
        try:
            payload = canonical_manifest_bytes(candidate)
        except ManifestRepositoryError as error:
            _map_manifest_error(error)
        source_payload_size = len(canonical_manifest_bytes(snapshot.document))
        if len(payload) < source_payload_size or len(payload) - source_payload_size > (
            2_048 + _MANIFEST_GROWTH_SAFETY_BYTES
        ):
            reject_signature_image(SignatureImageErrorCode.CAPACITY_EXCEEDED, "signature.manifest")

        identifiers = _manifest_identifiers(candidate)
        manifest_stage_id = _allocate_identifier(self._temp_id_factory, identifiers)
        identifiers.add(manifest_stage_id)
        journal_stage_id = _allocate_identifier(self._temp_id_factory, identifiers)
        manifest_path = prepared._authority.layout.temp / f"{manifest_stage_id}.tmp"
        journal_path = prepared._authority.layout.temp / f"{journal_stage_id}.tmp"
        manifest_reservation: WorkspaceCapacityReservation | None = None
        journal_reservation: WorkspaceCapacityReservation | None = None
        source_published = False
        preserve_asset = False
        failure: BaseException | None = None
        committed_revision: int | None = None

        def checkpoint() -> None:
            _require_active(cancellation, deadline)

        def publish_asset() -> None:
            nonlocal source_published
            authority = prepared._authority
            with prepared._source_reservation.publication_guard(
                case_identity=authority.identity,
                relative_parts=prepared._source_reservation.relative_parts,
                is_directory=False,
                byte_count=summary.byte_size,
                entry_count=1,
                coordination_checkpoint=checkpoint,
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
                        storage_name=asset.storage_name,
                    )
                    source_published = True

        try:
            manifest_reservation = self._manager.reserve_capacity(
                summary.case_id,
                QuotaArea.TEMP,
                relative_path=f"temp/{manifest_stage_id}.tmp",
                is_directory=False,
                byte_count=len(payload),
                coordination_checkpoint=checkpoint,
            )
            journal_reservation = self._manager.reserve_capacity(
                summary.case_id,
                QuotaArea.TEMP,
                relative_path=f"temp/{journal_stage_id}.tmp",
                is_directory=False,
                byte_count=len(payload),
                coordination_checkpoint=checkpoint,
            )
            _report(progress, summary.pending_id, SignatureImageProgressPhase.COMMITTING, 0, 1)
            saved = commit_manifest_transaction(
                authority=prepared._authority,
                expected_payload=None,
                expected_sha256=snapshot.sha256,
                expected_cas_token=snapshot.cas_token,
                candidate_payload=payload,
                manifest_stage_path=manifest_path,
                journal_stage_path=journal_path,
                manifest_reservation=manifest_reservation,
                journal_reservation=journal_reservation,
                before_checkpoint=publish_asset,
                coordination_checkpoint=checkpoint,
            )
            committed_revision = saved.revision
            preserve_asset = True
        except _ManifestTransactionInterruption as interruption:
            failure = interruption.cause
            preserve_asset = source_published and interruption.preserve_recovery
        except BaseException as error:
            failure = error
            if isinstance(error, ManifestRepositoryError):
                preserve_asset = source_published and error.context in (
                    _MANIFEST_RECOVERY_REQUIRED_CONTEXTS
                )
            elif source_published:
                preserve_asset = True
        finally:
            if journal_reservation is not None:
                journal_reservation.release()
            if manifest_reservation is not None:
                manifest_reservation.release()
            prepared._complete(preserve_published=preserve_asset)

        if committed_revision is not None:
            result = SignatureImagePlacementCommitResult(
                summary.case_id,
                summary.asset_id,
                overlay_id,
                committed_revision,
            )
            _report_best_effort(
                progress,
                summary.pending_id,
                SignatureImageProgressPhase.COMMITTING,
                1,
                1,
            )
            return result
        if isinstance(failure, SignatureImageError):
            raise failure
        if isinstance(failure, ManifestRepositoryError):
            _map_manifest_error(failure)
        if isinstance(failure, WorkspaceError):
            _map_workspace_error(failure, preparing=False)
        if isinstance(failure, Exception):
            reject_signature_image(SignatureImageErrorCode.COMMIT_FAILED, "signature.commit")
        if failure is not None:
            raise failure
        reject_signature_image(SignatureImageErrorCode.COMMIT_FAILED, "signature.commit")


def _copy_and_verify(
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
        _require_active(cancellation, deadline)
        count = min(MAXIMUM_STREAM_CHUNK_BYTES, byte_size - completed)
        read_at = getattr(source, "read_at", None)
        if not callable(read_at):
            reject_signature_image(SignatureImageErrorCode.COPY_FAILED, "signature.reader")
        chunk = read_at(completed, count)
        if type(chunk) is not bytes or len(chunk) != count:
            reject_signature_image(SignatureImageErrorCode.SOURCE_CHANGED, "signature.read")
        pending.write_chunk(chunk)
        source_digest.update(chunk)
        completed += count
        _report(progress, operation_id, SignatureImageProgressPhase.COPYING, completed, byte_size)
    pending.seal(expected_size=byte_size)
    pending.rewind()
    readback = hashlib.sha256()
    completed = 0
    while completed < byte_size:
        _require_active(cancellation, deadline)
        count = min(MAXIMUM_STREAM_CHUNK_BYTES, byte_size - completed)
        readback.update(pending.read_exact(count))
        completed += count
        _report(progress, operation_id, SignatureImageProgressPhase.VERIFYING, completed, byte_size)
    pending.require_eof()
    digest = source_digest.hexdigest()
    if not hmac.compare_digest(digest, readback.hexdigest()):
        reject_signature_image(SignatureImageErrorCode.COPY_FAILED, "signature.readback")
    pending.rewind()
    return digest


def _authenticate_asset_handle(
    handle: WindowsHandle,
    asset: OverlayAssetRecord,
    *,
    cancellation: CancellationProbe,
    deadline: DeadlineProbe,
    hash_progress: Callable[[int], None] | None = None,
) -> None:
    info = handle.info
    if (
        info.is_directory
        or info.is_reparse_point
        or info.is_sparse
        or info.link_count != 1
        or info.logical_size != asset.byte_size
    ):
        reject_signature_image(SignatureImageErrorCode.ASSET_CHANGED, "signature.identity")
    require_no_alternate_streams(info.final_path)
    handle.rewind()
    digest = hashlib.sha256()
    completed = 0
    while completed < asset.byte_size:
        _require_active(cancellation, deadline)
        count = min(MAXIMUM_STREAM_CHUNK_BYTES, asset.byte_size - completed)
        digest.update(handle.read_exact(count))
        completed += count
        if hash_progress is not None:
            hash_progress(completed)
    current = handle.require_eof()
    if (
        current.identity != info.identity
        or current.logical_size != asset.byte_size
        or current.is_directory
        or current.is_reparse_point
        or current.is_sparse
        or current.link_count != 1
        or not hmac.compare_digest(digest.hexdigest(), asset.sha256)
    ):
        reject_signature_image(SignatureImageErrorCode.ASSET_CHANGED, "signature.digest")
    require_no_alternate_streams(current.final_path)


def _require_prepared_candidate(
    candidate: dict[str, object],
    summary: PreparedSignatureImageSummary,
    *,
    source_document: dict[str, object],
    cleanup_disposition: SignatureAssetCleanupDisposition,
) -> tuple[OverlayAssetRecord, UUID]:
    collection = OverlayCollection.from_manifest_document(candidate)
    source_collection = OverlayCollection.from_manifest_document(source_document)
    asset = next(
        (item for item in collection.assets if item.asset_id == summary.asset_id),
        None,
    )
    if (
        asset is None
        or asset.kind is not OverlayAssetKind.SIGNATURE_IMAGE
        or asset.storage_name != f"{summary.asset_id}.bin"
        or asset.media_type is not _overlay_media_type(summary.media_type)
        or asset.byte_size != summary.byte_size
        or not hmac.compare_digest(asset.sha256, summary.sha256)
    ):
        reject_signature_image(SignatureImageErrorCode.CASE_CHANGED, "signature.asset_binding")
    overlay_ids = tuple(
        record.overlay_id
        for record in collection.overlays
        if type(record.payload) is SignatureImageOverlayPayload
        and record.payload.asset_id == summary.asset_id
    )
    if len(overlay_ids) != 1:
        reject_signature_image(SignatureImageErrorCode.CASE_CHANGED, "signature.overlay_binding")
    overlay_id = overlay_ids[0]
    overlay = next(record for record in collection.overlays if record.overlay_id == overlay_id)
    candidate_base = {
        key: value for key, value in candidate.items() if key not in {"assets", "overlays"}
    }
    source_base = {
        key: value for key, value in source_document.items() if key not in {"assets", "overlays"}
    }
    if (
        cleanup_disposition is not SignatureAssetCleanupDisposition.NONE
        or candidate_base != source_base
        or collection.assets != source_collection.assets + (asset,)
        or collection.overlays != source_collection.overlays + (overlay,)
    ):
        reject_signature_image(SignatureImageErrorCode.CASE_CHANGED, "signature.candidate_scope")
    return asset, overlay_id


def _manifest_references_original(document: dict[str, object], storage_name: str) -> bool:
    for collection_name in ("sources", "assets"):
        records = document.get(collection_name)
        if type(records) is not list:
            reject_signature_image(SignatureImageErrorCode.INTERNAL_ERROR, "signature.manifest")
        for raw_record in cast(list[object], records):
            if type(raw_record) is not dict:
                reject_signature_image(SignatureImageErrorCode.INTERNAL_ERROR, "signature.manifest")
            if cast(dict[str, object], raw_record).get("storage_name") == storage_name:
                return True
    return False


def _manifest_identifiers(document: dict[str, object]) -> set[UUID]:
    identifiers: set[UUID] = set()
    operations: tuple[dict[str, object], ...] = ()
    for collection_name, field_name in (
        ("sources", "source_id"),
        ("pages", "page_id"),
        ("page_operations", "operation_id"),
        ("assets", "asset_id"),
        ("overlays", "overlay_id"),
        ("assignments", "assignment_id"),
        ("confirmations", "confirmation_id"),
    ):
        raw = document.get(collection_name)
        if type(raw) is not list:
            reject_signature_image(SignatureImageErrorCode.INTERNAL_ERROR, "signature.manifest")
        if collection_name == "page_operations":
            operations = tuple(
                cast(dict[str, object], record)
                for record in cast(list[object], raw)
                if type(record) is dict
            )
        for record in cast(list[object], raw):
            if type(record) is not dict:
                reject_signature_image(SignatureImageErrorCode.INTERNAL_ERROR, "signature.manifest")
            value = cast(dict[str, object], record).get(field_name)
            if type(value) is not str:
                reject_signature_image(SignatureImageErrorCode.INTERNAL_ERROR, "signature.manifest")
            try:
                identifiers.add(UUID(value))
            except ValueError:
                reject_signature_image(SignatureImageErrorCode.INTERNAL_ERROR, "signature.manifest")
    for operation in operations:
        payload = operation.get("payload")
        if type(payload) is not dict:
            reject_signature_image(SignatureImageErrorCode.INTERNAL_ERROR, "signature.manifest")
        if payload.get("kind") == "set_group":
            value = payload.get("group_id")
            if type(value) is not str:
                reject_signature_image(SignatureImageErrorCode.INTERNAL_ERROR, "signature.manifest")
            try:
                identifiers.add(UUID(value))
            except ValueError:
                reject_signature_image(SignatureImageErrorCode.INTERNAL_ERROR, "signature.manifest")
    export_history = document.get("export_history")
    if type(export_history) is not dict or type(export_history.get("entries")) is not list:
        reject_signature_image(SignatureImageErrorCode.INTERNAL_ERROR, "signature.manifest")
    for raw_entry in cast(list[object], export_history["entries"]):
        if type(raw_entry) is not dict or type(raw_entry.get("export_id")) is not str:
            reject_signature_image(SignatureImageErrorCode.INTERNAL_ERROR, "signature.manifest")
        try:
            identifiers.add(UUID(cast(str, raw_entry["export_id"])))
        except ValueError:
            reject_signature_image(SignatureImageErrorCode.INTERNAL_ERROR, "signature.manifest")
    identifiers.add(UUID(cast(str, document["case_id"])))
    return identifiers


def _allocate_identifier(factory: Callable[[], UUID], reserved: set[UUID]) -> UUID:
    for _ in range(_MAXIMUM_IDENTIFIER_ATTEMPTS):
        try:
            candidate = factory()
        except BaseException:
            continue
        if type(candidate) is UUID and candidate.version == 4 and candidate not in reserved:
            return candidate
    reject_signature_image(SignatureImageErrorCode.INTERNAL_ERROR, "signature.identifier")


def _require_uuid(value: object, context: str) -> UUID:
    if type(value) is not UUID or value.version != 4:
        reject_signature_image(SignatureImageErrorCode.MODEL_INVALID, context)
    return value


def _require_clock(clock: Callable[[], datetime]) -> datetime:
    try:
        value = clock()
    except BaseException:
        reject_signature_image(SignatureImageErrorCode.INTERNAL_ERROR, "signature.clock")
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        reject_signature_image(SignatureImageErrorCode.INTERNAL_ERROR, "signature.clock")
    return value.astimezone(UTC)


def _require_active(cancellation: CancellationProbe, deadline: DeadlineProbe) -> None:
    try:
        cancelled = cancellation()
        expired = deadline()
    except BaseException:
        reject_signature_image(SignatureImageErrorCode.INTERNAL_ERROR, "signature.control")
    if type(cancelled) is not bool or type(expired) is not bool:
        reject_signature_image(SignatureImageErrorCode.INTERNAL_ERROR, "signature.control")
    if cancelled:
        reject_signature_image(SignatureImageErrorCode.CANCELLED, "signature.cancelled")
    if expired:
        reject_signature_image(SignatureImageErrorCode.TIMED_OUT, "signature.deadline")


def _report(
    progress: ProgressSink,
    operation_id: UUID,
    phase: SignatureImageProgressPhase,
    completed: int,
    total: int,
) -> None:
    try:
        progress(SignatureImageProgress(operation_id, phase, completed, total))
    except SignatureImageError:
        raise
    except BaseException:
        reject_signature_image(SignatureImageErrorCode.INTERNAL_ERROR, "signature.progress")


def _report_best_effort(
    progress: ProgressSink,
    operation_id: UUID,
    phase: SignatureImageProgressPhase,
    completed: int,
    total: int,
) -> None:
    """Never hide a durable operation result behind terminal progress failure."""

    try:
        progress(SignatureImageProgress(operation_id, phase, completed, total))
    except BaseException:
        pass


def _detected_media_type(value: DetectedMediaType) -> SignatureImageMediaType:
    if value is DetectedMediaType.JPEG:
        return SignatureImageMediaType.JPEG
    if value is DetectedMediaType.PNG:
        return SignatureImageMediaType.PNG
    reject_signature_image(SignatureImageErrorCode.MEDIA_UNSUPPORTED, "signature.media")


def _overlay_media_type(value: SignatureImageMediaType) -> OverlayAssetMediaType:
    return (
        OverlayAssetMediaType.JPEG
        if value is SignatureImageMediaType.JPEG
        else OverlayAssetMediaType.PNG
    )


def _signature_media_type(value: OverlayAssetMediaType) -> SignatureImageMediaType:
    return (
        SignatureImageMediaType.JPEG
        if value is OverlayAssetMediaType.JPEG
        else SignatureImageMediaType.PNG
    )


def _map_precheck_error(error: ImportPrecheckError) -> Never:
    if error.code in {
        ImportPrecheckErrorCode.SOURCE_PATH_INVALID,
        ImportPrecheckErrorCode.SOURCE_LOCATION_UNSUPPORTED,
    }:
        code = SignatureImageErrorCode.SOURCE_PATH_INVALID
    elif error.code in {
        ImportPrecheckErrorCode.MEDIA_UNSUPPORTED,
        ImportPrecheckErrorCode.EXTENSION_MISMATCH,
    }:
        code = SignatureImageErrorCode.MEDIA_UNSUPPORTED
    elif error.code is ImportPrecheckErrorCode.SOURCE_CHANGED:
        code = SignatureImageErrorCode.SOURCE_CHANGED
    elif error.code in _PRECHECK_LIMIT_ERRORS:
        code = SignatureImageErrorCode.LIMIT_EXCEEDED
    elif error.code in _PRECHECK_MALFORMED_ERRORS:
        code = SignatureImageErrorCode.MALFORMED
    else:
        code = SignatureImageErrorCode.SOURCE_UNAVAILABLE
    reject_signature_image(
        code,
        "signature.precheck",
    )


def _map_manifest_error(error: ManifestRepositoryError) -> Never:
    code = (
        SignatureImageErrorCode.CASE_CHANGED
        if error.code is ManifestRepositoryErrorCode.REVISION_CONFLICT
        else SignatureImageErrorCode.COMMIT_FAILED
    )
    reject_signature_image(code, "signature.manifest")


def _map_workspace_error(error: WorkspaceError, *, preparing: bool) -> Never:
    if error.code is WorkspaceErrorCode.QUOTA_EXCEEDED:
        reject_signature_image(SignatureImageErrorCode.CAPACITY_EXCEEDED, "signature.capacity")
    reject_signature_image(
        SignatureImageErrorCode.COPY_FAILED if preparing else SignatureImageErrorCode.COMMIT_FAILED,
        "signature.storage",
    )


def _delete_pending_no_raise(pending: WindowsPendingSourceFile) -> None:
    try:
        pending.mark_for_deletion()
    except BaseException:
        pass
    try:
        pending.close()
    except BaseException:
        pass


def _close_pending_no_raise(pending: WindowsPendingSourceFile) -> None:
    try:
        pending.close()
    except BaseException:
        pass

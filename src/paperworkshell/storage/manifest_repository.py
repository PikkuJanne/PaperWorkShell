"""Authenticated atomic manifest persistence and one-snapshot recovery journal."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Final, Never, cast
from uuid import UUID, uuid4

from paperworkshell.domain.case_lifecycle import (
    CURRENT_CASE_MANIFEST_VERSION,
    INTERMEDIATE_CASE_MANIFEST_VERSION,
    LEGACY_CASE_MANIFEST_VERSION,
    REMOVAL_CASE_MANIFEST_VERSION,
)
from paperworkshell.domain.manifest_migrations import (
    migrate_manifest_to_current,
)
from paperworkshell.domain.manifest_repository import (
    MAX_SAFE_MANIFEST_INTEGER,
    ManifestLoadResult,
    ManifestLoadStatus,
    ManifestRepositoryError,
    ManifestRepositoryErrorCode,
    ManifestSaveResult,
    ManifestSaveStatus,
    ManifestSnapshot,
)
from paperworkshell.domain.manifest_validation import validate_manifest
from paperworkshell.storage._windows import (
    FileIdentity,
    ManifestReplaceState,
    WindowsHandle,
    open_handle,
    publish_new_file,
    publish_new_journal_checkpoint,
    reopen_read_only_file,
    replace_journal_checkpoint_atomically,
    replace_manifest_atomically,
    require_expected_handle,
    require_no_alternate_streams,
    verify_private_acl,
    write_new_file,
)
from paperworkshell.storage.errors import WorkspaceError, WorkspaceErrorCode
from paperworkshell.storage.manifest_codec import (
    MAX_MANIFEST_BYTES,
    decode_manifest,
    parse_utc_timestamp,
    probe_supported_manifest_schema_version,
    summary_from_manifest,
)
from paperworkshell.storage.manifest_migration_backup import retain_migration_source
from paperworkshell.storage.quotas import QuotaArea
from paperworkshell.storage.workspace import (
    AuthenticatedCase,
    CaseWorkspaceManager,
    WorkspaceCapacityReservation,
)

_SCHEMA_RESOURCES: Final = {
    LEGACY_CASE_MANIFEST_VERSION: "case-manifest-v1.schema.json",
    INTERMEDIATE_CASE_MANIFEST_VERSION: "case-manifest-v2.schema.json",
    REMOVAL_CASE_MANIFEST_VERSION: "case-manifest-v3.schema.json",
    CURRENT_CASE_MANIFEST_VERSION: "case-manifest-v4.schema.json",
}
_SCHEMA_FINGERPRINTS: Final = {
    LEGACY_CASE_MANIFEST_VERSION: (
        "a4dfbc925b601ce3dca08085173a742642cab6b962b4358f91d921bd9fdbd695"
    ),
    INTERMEDIATE_CASE_MANIFEST_VERSION: (
        "af27d3d3569d660638371d840a35b754335b074021c39d20cc4f282df4c7e599"
    ),
    REMOVAL_CASE_MANIFEST_VERSION: (
        "58d77bb8d279c2fae40f0a5fb050a34a851146ff4e794fa2765b87de543defb4"
    ),
    CURRENT_CASE_MANIFEST_VERSION: (
        "481ce360cdb81c50512225bb8fc9f647f15b88d31adf0d74239aa3ea0960c6f4"
    ),
}
_MAX_SCHEMA_BYTES: Final = 2 * 1024 * 1024
_JOURNAL_CHECKPOINT_NAME: Final = "latest.json"
_SHA256_LENGTH: Final = 64


@dataclass(frozen=True, slots=True)
class _ValidatedManifest:
    payload: bytes = field(repr=False)
    document: dict[str, object] = field(repr=False)
    case_id: UUID
    schema_version: int
    revision: int
    created_at: datetime
    updated_at: datetime
    sha256: str = field(repr=False)

    def snapshot(self, identity: FileIdentity) -> ManifestSnapshot:
        return ManifestSnapshot(
            case_id=self.case_id,
            schema_version=self.schema_version,
            revision=self.revision,
            document=self.document,
            sha256=self.sha256,
            cas_token=_cas_token(identity, self.sha256),
        )


@dataclass(frozen=True, slots=True)
class _ObservedManifest:
    validated: _ValidatedManifest
    identity: FileIdentity

    @property
    def snapshot(self) -> ManifestSnapshot:
        return self.validated.snapshot(self.identity)


@dataclass(frozen=True, slots=True)
class _RawManifest:
    payload: bytes = field(repr=False)
    identity: FileIdentity


BeforeCheckpoint = Callable[[], None]
CoordinationCheckpoint = Callable[[], None]


class _ManifestTransactionInterruption(BaseException):
    """Carry a process-control exception through exact recovery cleanup without rendering it."""

    __slots__ = ("cause", "preserve_recovery")

    def __init__(self, cause: BaseException, *, preserve_recovery: bool) -> None:
        super().__init__()
        self.cause = cause
        self.preserve_recovery = preserve_recovery


class WindowsManifestRepository:
    """Save and recover complete case manifests through retained Windows handles."""

    __slots__ = ("_id_factory", "_manager", "_migrate_on_load")

    def __init__(
        self,
        manager: CaseWorkspaceManager,
        *,
        id_factory: Callable[[], UUID] = uuid4,
        migrate_on_load: bool = True,
    ) -> None:
        if not callable(id_factory) or type(migrate_on_load) is not bool:
            _reject(ManifestRepositoryErrorCode.MODEL_INVALID, "manifest.repository")
        self._manager = manager
        self._id_factory = id_factory
        self._migrate_on_load = migrate_on_load

    def load(
        self,
        case_id: UUID,
        *,
        coordination_checkpoint: CoordinationCheckpoint | None = None,
        migrate: bool | None = None,
    ) -> ManifestLoadResult:
        """Recover a valid record; migrate only for an explicit case-open operation.

        Startup recovery passes ``migrate=False`` so inspecting unrelated closed
        cases does not initiate a schema upgrade. Existing checkpoint recovery
        remains available for every supported version.
        """

        selected = _require_case_id(case_id, context="manifest.load_case")
        if migrate is None:
            migrate = self._migrate_on_load
        if type(migrate) is not bool:
            _reject(ManifestRepositoryErrorCode.MODEL_INVALID, "manifest.load_migration")
        if coordination_checkpoint is not None and not callable(coordination_checkpoint):
            _reject(ManifestRepositoryErrorCode.MODEL_INVALID, "manifest.load_checkpoint")

        def guarded_checkpoint() -> None:
            if coordination_checkpoint is None:
                return
            try:
                coordination_checkpoint()
            except BaseException as error:
                raise _ManifestTransactionInterruption(
                    error,
                    preserve_recovery=False,
                ) from None

        checkpoint = guarded_checkpoint if coordination_checkpoint is not None else None
        try:
            with self._manager.authenticate_case(
                selected,
                coordination_checkpoint=checkpoint,
            ) as authority:
                with authority.transaction_guard(coordination_checkpoint=checkpoint):
                    return self._load_locked(authority, migrate=migrate)
        except ManifestRepositoryError:
            raise
        except _ManifestTransactionInterruption as interruption:
            raise interruption.cause from None
        except WorkspaceError as error:
            _map_workspace_error(error, context="manifest.load_storage")
        except Exception:
            _reject(ManifestRepositoryErrorCode.STORAGE_FAILED, "manifest.load_storage")

    def save(
        self,
        case_id: UUID,
        candidate: Mapping[str, object],
        *,
        expected_revision: int,
        expected_sha256: str,
        expected_cas_token: str,
    ) -> ManifestSaveResult:
        """Validate, checkpoint, and atomically publish exactly the expected next revision."""

        selected = _require_case_id(case_id, context="manifest.save_case")
        _require_expected_save_tokens(
            expected_revision,
            expected_sha256,
            expected_cas_token,
        )
        candidate_payload = _canonical_manifest_bytes(candidate)
        target = _validate_manifest_payload(candidate_payload, selected)
        if target.revision != expected_revision + 1:
            _reject(ManifestRepositoryErrorCode.REVISION_CONFLICT, "manifest.save_revision")

        # Candidate version/case/schema validation intentionally precedes any recovery mutation.
        opened = self.load(selected).snapshot
        if (
            opened.revision != expected_revision
            or opened.sha256 != expected_sha256
            or opened.cas_token != expected_cas_token
        ):
            _reject(ManifestRepositoryErrorCode.REVISION_CONFLICT, "manifest.save_cas")
        current_document = opened.document
        if (
            target.schema_version != opened.schema_version
            or target.document["created_at"] != current_document["created_at"]
            or target.updated_at < _parse_manifest_timestamp(current_document["updated_at"])
        ):
            _reject(ManifestRepositoryErrorCode.MANIFEST_INVALID, "manifest.save_metadata")

        manifest_stage_id = _next_internal_id(self._id_factory, context="manifest.stage_id")
        journal_stage_id = _next_internal_id(self._id_factory, context="manifest.journal_stage_id")
        layout = self._manager.layout(selected)
        manifest_stage_path = layout.temp / f"{manifest_stage_id}.tmp"
        journal_stage_path = layout.temp / f"{journal_stage_id}.tmp"
        manifest_reservation: WorkspaceCapacityReservation | None = None
        journal_reservation: WorkspaceCapacityReservation | None = None
        try:
            manifest_reservation = self._manager.reserve_capacity(
                selected,
                QuotaArea.TEMP,
                relative_path=f"temp/{manifest_stage_id}.tmp",
                is_directory=False,
                byte_count=len(candidate_payload),
            )
            journal_reservation = self._manager.reserve_capacity(
                selected,
                QuotaArea.TEMP,
                relative_path=f"temp/{journal_stage_id}.tmp",
                is_directory=False,
                byte_count=len(candidate_payload),
            )
            with self._manager.authenticate_case(selected) as authority:
                committed = commit_manifest_transaction(
                    authority=authority,
                    expected_payload=None,
                    expected_sha256=expected_sha256,
                    expected_cas_token=expected_cas_token,
                    candidate_payload=candidate_payload,
                    manifest_stage_path=manifest_stage_path,
                    journal_stage_path=journal_stage_path,
                    manifest_reservation=manifest_reservation,
                    journal_reservation=journal_reservation,
                )
            return ManifestSaveResult(
                status=ManifestSaveStatus.SAVED,
                snapshot=committed,
            )
        except ManifestRepositoryError:
            raise
        except _ManifestTransactionInterruption as interruption:
            raise interruption.cause from None
        except WorkspaceError as error:
            _map_workspace_error(error, context="manifest.save_storage")
        except Exception:
            _reject(ManifestRepositoryErrorCode.SAVE_FAILED, "manifest.save_storage")
        finally:
            if journal_reservation is not None:
                journal_reservation.release()
            if manifest_reservation is not None:
                manifest_reservation.release()

    def _load_locked(
        self, authority: AuthenticatedCase, *, migrate: bool = True
    ) -> ManifestLoadResult:
        current_raw: _RawManifest | None = None
        current: _ObservedManifest | None = None
        current_error: ManifestRepositoryError | None = None
        try:
            current_raw = _read_raw_manifest(authority)
            validated = _validate_loadable_manifest_payload(current_raw.payload, authority.case_id)
            current = _ObservedManifest(validated, current_raw.identity)
        except ManifestRepositoryError as error:
            current_error = error

        if (
            current_error is not None
            and current_error.code is ManifestRepositoryErrorCode.VERSION_UNSUPPORTED
        ):
            raise current_error

        checkpoint: _ValidatedManifest | None = None
        checkpoint_error: ManifestRepositoryError | None = None
        try:
            checkpoint = _read_checkpoint(authority)
        except ManifestRepositoryError as error:
            checkpoint_error = error

        if (
            checkpoint_error is not None
            and checkpoint_error.code is ManifestRepositoryErrorCode.VERSION_UNSUPPORTED
        ):
            raise checkpoint_error

        if current is not None:
            if checkpoint is None:
                return self._finish_loaded_locked(
                    authority,
                    current.snapshot,
                    ManifestLoadStatus.CURRENT,
                    migrate=migrate,
                )
            if (
                checkpoint.case_id == current.validated.case_id
                and checkpoint.revision == current.validated.revision + 1
                and checkpoint.document["created_at"] == current.validated.document["created_at"]
            ):
                if checkpoint.schema_version < current.validated.schema_version:
                    _reject(
                        ManifestRepositoryErrorCode.RECOVERY_FAILED,
                        "manifest.recovery_version",
                    )
                restored = self._restore_locked(
                    authority,
                    checkpoint,
                    current_raw=current_raw,
                )
                return self._finish_loaded_locked(
                    authority,
                    restored,
                    ManifestLoadStatus.RECOVERED,
                    migrate=migrate,
                    recovered_migration=(
                        current.validated.schema_version < CURRENT_CASE_MANIFEST_VERSION
                        and restored.schema_version == CURRENT_CASE_MANIFEST_VERSION
                    ),
                )
            if checkpoint.revision > current.validated.revision + 1:
                _reject(ManifestRepositoryErrorCode.RECOVERY_FAILED, "manifest.recovery_gap")
            return self._finish_loaded_locked(
                authority,
                current.snapshot,
                ManifestLoadStatus.CURRENT,
                migrate=migrate,
            )

        if checkpoint is None:
            if current_error is not None:
                raise current_error
            _reject(ManifestRepositoryErrorCode.RECOVERY_FAILED, "manifest.recovery_missing")
        restored = self._restore_locked(authority, checkpoint, current_raw=current_raw)
        return self._finish_loaded_locked(
            authority,
            restored,
            ManifestLoadStatus.RECOVERED,
            migrate=migrate,
        )

    def _finish_loaded_locked(
        self,
        authority: AuthenticatedCase,
        snapshot: ManifestSnapshot,
        status: ManifestLoadStatus,
        *,
        recovered_migration: bool = False,
        migrate: bool = True,
    ) -> ManifestLoadResult:
        """Return only v4, transactionally migrating a settled supported older state."""

        if snapshot.schema_version == CURRENT_CASE_MANIFEST_VERSION or not migrate:
            selected_status = (
                ManifestLoadStatus.RECOVERED_MIGRATED if recovered_migration else status
            )
            return ManifestLoadResult(selected_status, snapshot)
        if snapshot.schema_version not in {
            LEGACY_CASE_MANIFEST_VERSION,
            INTERMEDIATE_CASE_MANIFEST_VERSION,
            REMOVAL_CASE_MANIFEST_VERSION,
        }:
            _reject(ManifestRepositoryErrorCode.VERSION_UNSUPPORTED, "manifest.migration_version")
        migrated = self._migrate_to_current_locked(authority, snapshot)
        selected_status = (
            ManifestLoadStatus.RECOVERED_MIGRATED
            if status is ManifestLoadStatus.RECOVERED
            else ManifestLoadStatus.MIGRATED
        )
        return ManifestLoadResult(selected_status, migrated)

    def _migrate_to_current_locked(
        self,
        authority: AuthenticatedCase,
        source: ManifestSnapshot,
    ) -> ManifestSnapshot:
        """Publish a copy-only transition to v4 through one journal boundary."""

        if (
            source.case_id != authority.case_id
            or source.schema_version
            not in {
                LEGACY_CASE_MANIFEST_VERSION,
                INTERMEDIATE_CASE_MANIFEST_VERSION,
                REMOVAL_CASE_MANIFEST_VERSION,
            }
            or source.revision >= MAX_SAFE_MANIFEST_INTEGER
        ):
            _reject(ManifestRepositoryErrorCode.MANIFEST_INVALID, "manifest.migration_source")
        candidate = migrate_manifest_to_current(source.document)
        candidate_payload = _canonical_manifest_bytes(candidate)
        _validate_manifest_payload(candidate_payload, authority.case_id)
        observed_source = _read_raw_manifest(authority)
        if hashlib.sha256(observed_source.payload).hexdigest() != source.sha256:
            _reject(ManifestRepositoryErrorCode.REVISION_CONFLICT, "manifest.migration_source")
        retain_migration_source(
            self._manager,
            authority,
            payload=observed_source.payload,
            schema_version=source.schema_version,
            id_factory=self._id_factory,
        )
        manifest_stage_id = _next_internal_id(
            self._id_factory,
            context="manifest.migration_stage_id",
        )
        journal_stage_id = _next_internal_id(
            self._id_factory,
            context="manifest.migration_journal_stage_id",
        )
        manifest_stage_path = authority.layout.temp / f"{manifest_stage_id}.tmp"
        journal_stage_path = authority.layout.temp / f"{journal_stage_id}.tmp"
        manifest_reservation: WorkspaceCapacityReservation | None = None
        journal_reservation: WorkspaceCapacityReservation | None = None
        try:
            manifest_reservation = self._manager.reserve_capacity(
                authority.case_id,
                QuotaArea.TEMP,
                relative_path=f"temp/{manifest_stage_id}.tmp",
                is_directory=False,
                byte_count=len(candidate_payload),
            )
            journal_reservation = self._manager.reserve_capacity(
                authority.case_id,
                QuotaArea.TEMP,
                relative_path=f"temp/{journal_stage_id}.tmp",
                is_directory=False,
                byte_count=len(candidate_payload),
            )
            return commit_manifest_transaction(
                authority=authority,
                expected_payload=None,
                expected_sha256=source.sha256,
                expected_cas_token=source.cas_token,
                candidate_payload=candidate_payload,
                manifest_stage_path=manifest_stage_path,
                journal_stage_path=journal_stage_path,
                manifest_reservation=manifest_reservation,
                journal_reservation=journal_reservation,
                allow_legacy_current=True,
            )
        finally:
            if journal_reservation is not None:
                journal_reservation.release()
            if manifest_reservation is not None:
                manifest_reservation.release()

    def _restore_locked(
        self,
        authority: AuthenticatedCase,
        checkpoint: _ValidatedManifest,
        *,
        current_raw: _RawManifest | None,
    ) -> ManifestSnapshot:
        stage_id = _next_internal_id(self._id_factory, context="manifest.recovery_stage_id")
        stage_path = authority.layout.temp / f"{stage_id}.tmp"
        reservation: WorkspaceCapacityReservation | None = None
        stage_handle: WindowsHandle | None = None
        committed = False
        preserve = False
        try:
            reservation = self._manager.reserve_capacity(
                authority.case_id,
                QuotaArea.TEMP,
                relative_path=f"temp/{stage_id}.tmp",
                is_directory=False,
                byte_count=len(checkpoint.payload),
            )
            with reservation.publication_guard(
                case_identity=authority.identity,
                relative_parts=reservation.relative_parts,
                is_directory=False,
                byte_count=len(checkpoint.payload),
                entry_count=1,
            ):
                stage_handle = write_new_file(stage_path, checkpoint.payload)
                _reopen_and_validate_stage(
                    stage_handle,
                    checkpoint,
                    authority.case_id,
                    allow_legacy=True,
                )
                if current_raw is None:
                    publish_new_file(
                        stage_handle,
                        authority.handle,
                        destination=authority.layout.manifest,
                    )
                    committed = True
                else:
                    expected_handle: WindowsHandle | None = None
                    try:
                        expected_handle = open_handle(
                            authority.layout.manifest,
                            read=True,
                            delete=True,
                            share_delete=True,
                        )
                        expected = _read_exact_handle(
                            expected_handle,
                            authority.layout.manifest,
                        )
                        if (
                            expected.identity != current_raw.identity
                            or expected.payload != current_raw.payload
                        ):
                            _reject(
                                ManifestRepositoryErrorCode.REVISION_CONFLICT,
                                "manifest.recovery_cas",
                            )
                        outcome = replace_manifest_atomically(
                            stage_handle,
                            authority.handle,
                            expected_handle,
                        )
                        if outcome.state is ManifestReplaceState.COMMITTED:
                            committed = True
                        elif outcome.state is ManifestReplaceState.UNKNOWN:
                            preserve = True
                            _reject(
                                ManifestRepositoryErrorCode.COMMIT_UNCERTAIN,
                                "manifest.recovery_uncertain",
                            )
                        else:
                            _reject(
                                ManifestRepositoryErrorCode.RECOVERY_FAILED,
                                "manifest.recovery_replace",
                            )
                    finally:
                        _close_handle(expected_handle)
                if not committed:
                    _reject(
                        ManifestRepositoryErrorCode.RECOVERY_FAILED,
                        "manifest.recovery_commit",
                    )
                _close_handle(stage_handle)
                stage_handle = None
                reloaded = _read_observed_manifest(authority, allow_legacy=True)
                if reloaded.validated.sha256 != checkpoint.sha256:
                    _reject(
                        ManifestRepositoryErrorCode.RECOVERY_FAILED,
                        "manifest.recovery_verify",
                    )
                return reloaded.snapshot
        except ManifestRepositoryError:
            raise
        except WorkspaceError as error:
            _map_workspace_error(error, context="manifest.recovery_storage")
        except Exception:
            _reject(ManifestRepositoryErrorCode.RECOVERY_FAILED, "manifest.recovery_storage")
        finally:
            if stage_handle is not None:
                if not committed and not preserve:
                    _delete_handle(stage_handle)
                else:
                    _close_handle(stage_handle)
            if reservation is not None:
                reservation.release()


def commit_manifest_transaction(
    *,
    authority: AuthenticatedCase,
    expected_payload: bytes | None,
    expected_sha256: str,
    expected_cas_token: str | None,
    candidate_payload: bytes,
    manifest_stage_path: Path,
    journal_stage_path: Path,
    manifest_reservation: WorkspaceCapacityReservation,
    journal_reservation: WorkspaceCapacityReservation,
    before_checkpoint: BeforeCheckpoint | None = None,
    coordination_checkpoint: CoordinationCheckpoint | None = None,
    allow_legacy_current: bool = False,
) -> ManifestSnapshot:
    """Commit one validated manifest and optional source publication as one journal boundary."""

    if (
        type(expected_payload) not in {bytes, type(None)}
        or type(expected_sha256) is not str
        or len(expected_sha256) != _SHA256_LENGTH
        or type(expected_cas_token) not in {str, type(None)}
        or type(candidate_payload) is not bytes
        or not isinstance(manifest_stage_path, Path)
        or not isinstance(journal_stage_path, Path)
        or not isinstance(manifest_reservation, WorkspaceCapacityReservation)
        or not isinstance(journal_reservation, WorkspaceCapacityReservation)
        or (before_checkpoint is not None and not callable(before_checkpoint))
        or type(allow_legacy_current) is not bool
    ):
        _reject(ManifestRepositoryErrorCode.MODEL_INVALID, "manifest.transaction")
    candidate = _validate_manifest_payload(candidate_payload, authority.case_id)
    if (
        len(candidate_payload) > manifest_reservation.byte_count
        or len(candidate_payload) > journal_reservation.byte_count
    ):
        _reject(ManifestRepositoryErrorCode.CAPACITY_EXCEEDED, "manifest.transaction_capacity")

    manifest_handle: WindowsHandle | None = None
    journal_handle: WindowsHandle | None = None
    expected_manifest: WindowsHandle | None = None
    expected_checkpoint: WindowsHandle | None = None
    checkpoint_committed = False
    checkpoint_uncertain = False
    manifest_committed = False
    manifest_uncertain = False
    committed_snapshot: ManifestSnapshot | None = None
    failure: BaseException | None = None
    try:
        with authority.transaction_guard(
            coordination_checkpoint=coordination_checkpoint,
        ):
            with manifest_reservation.publication_guard(
                case_identity=authority.identity,
                relative_parts=manifest_reservation.relative_parts,
                is_directory=False,
                byte_count=len(candidate_payload),
                entry_count=1,
                coordination_checkpoint=coordination_checkpoint,
            ):
                with journal_reservation.publication_guard(
                    case_identity=authority.identity,
                    relative_parts=journal_reservation.relative_parts,
                    is_directory=False,
                    byte_count=len(candidate_payload),
                    entry_count=1,
                    coordination_checkpoint=coordination_checkpoint,
                ):
                    expected_manifest = open_handle(
                        authority.layout.manifest,
                        read=True,
                        delete=True,
                        share_delete=True,
                    )
                    current_raw = _read_exact_handle(
                        expected_manifest,
                        authority.layout.manifest,
                    )
                    current = (
                        _validate_loadable_manifest_payload(
                            current_raw.payload,
                            authority.case_id,
                        )
                        if allow_legacy_current
                        else _validate_manifest_payload(current_raw.payload, authority.case_id)
                    )
                    if (
                        current.sha256 != expected_sha256
                        or (
                            expected_payload is not None and current_raw.payload != expected_payload
                        )
                        or (
                            expected_cas_token is not None
                            and _cas_token(current_raw.identity, current.sha256)
                            != expected_cas_token
                        )
                    ):
                        _reject(
                            ManifestRepositoryErrorCode.REVISION_CONFLICT,
                            "manifest.transaction_cas",
                        )
                    if (
                        candidate.revision != current.revision + 1
                        or candidate.document["created_at"] != current.document["created_at"]
                        or candidate.updated_at < current.updated_at
                    ):
                        _reject(
                            ManifestRepositoryErrorCode.REVISION_CONFLICT,
                            "manifest.transaction_revision",
                        )

                    # A valid newer checkpoint is authoritative recovery state. Never let
                    # a direct transaction (notably source ingest) replace it from the stale
                    # current manifest. Exact-candidate retry is safe and remains permitted.
                    try:
                        pending_checkpoint = _read_checkpoint(authority)
                    except ManifestRepositoryError as error:
                        if error.code is ManifestRepositoryErrorCode.VERSION_UNSUPPORTED:
                            raise
                        pending_checkpoint = None
                    if pending_checkpoint is not None:
                        if pending_checkpoint.revision > current.revision + 1:
                            _reject(
                                ManifestRepositoryErrorCode.RECOVERY_FAILED,
                                "manifest.transaction_recovery_gap",
                            )
                        if (
                            pending_checkpoint.revision == current.revision + 1
                            and pending_checkpoint.sha256 != candidate.sha256
                        ):
                            _reject(
                                ManifestRepositoryErrorCode.REVISION_CONFLICT,
                                "manifest.transaction_pending_checkpoint",
                            )

                    manifest_handle = write_new_file(manifest_stage_path, candidate_payload)
                    journal_handle = write_new_file(journal_stage_path, candidate_payload)
                    _reopen_and_validate_stage(manifest_handle, candidate, authority.case_id)
                    _reopen_and_validate_stage(journal_handle, candidate, authority.case_id)

                    if before_checkpoint is not None:
                        try:
                            before_checkpoint()
                        except BaseException as error:
                            if not isinstance(error, Exception):
                                raise _ManifestTransactionInterruption(
                                    error,
                                    preserve_recovery=False,
                                ) from None
                            raise

                    with open_handle(authority.layout.journal) as journal_root:
                        require_expected_handle(
                            journal_root,
                            authority.layout.journal,
                            directory=True,
                        )
                        verify_private_acl(journal_root)
                        require_no_alternate_streams(authority.layout.journal)
                        checkpoint_path = authority.layout.journal / _JOURNAL_CHECKPOINT_NAME
                        checkpoint_expected_identity: FileIdentity | None = None
                        checkpoint_delayed: BaseException | None = None
                        if os.path.lexists(checkpoint_path):
                            expected_checkpoint = open_handle(
                                checkpoint_path,
                                read=True,
                                delete=True,
                                share_delete=True,
                            )
                            _read_exact_handle(expected_checkpoint, checkpoint_path)
                            checkpoint_expected_identity = expected_checkpoint.info.identity
                            try:
                                checkpoint_outcome = replace_journal_checkpoint_atomically(
                                    journal_handle,
                                    authority.handle,
                                    journal_root,
                                    expected_checkpoint,
                                )
                            except BaseException as error:
                                checkpoint_delayed = error
                                checkpoint_state = _resolve_atomic_destination(
                                    checkpoint_path,
                                    candidate_identity=journal_handle.info.identity,
                                    expected_identity=checkpoint_expected_identity,
                                )
                            else:
                                checkpoint_state = checkpoint_outcome.state
                                checkpoint_delayed = checkpoint_outcome.delayed_exception
                        else:
                            try:
                                checkpoint_outcome = publish_new_journal_checkpoint(
                                    journal_handle,
                                    authority.handle,
                                    journal_root,
                                )
                            except BaseException as error:
                                checkpoint_delayed = error
                                checkpoint_state = _resolve_atomic_destination(
                                    checkpoint_path,
                                    candidate_identity=journal_handle.info.identity,
                                    expected_identity=None,
                                )
                            else:
                                checkpoint_state = checkpoint_outcome.state
                                checkpoint_delayed = checkpoint_outcome.delayed_exception
                    if checkpoint_state is ManifestReplaceState.COMMITTED:
                        checkpoint_committed = True
                    elif checkpoint_state is ManifestReplaceState.UNKNOWN:
                        checkpoint_uncertain = True
                        if checkpoint_delayed is not None and not isinstance(
                            checkpoint_delayed, Exception
                        ):
                            raise _ManifestTransactionInterruption(
                                checkpoint_delayed,
                                preserve_recovery=True,
                            )
                        _reject(
                            ManifestRepositoryErrorCode.COMMIT_UNCERTAIN,
                            "manifest.checkpoint_uncertain",
                        )
                    else:
                        if checkpoint_delayed is not None and not isinstance(
                            checkpoint_delayed, Exception
                        ):
                            raise _ManifestTransactionInterruption(
                                checkpoint_delayed,
                                preserve_recovery=False,
                            )
                        _reject(
                            ManifestRepositoryErrorCode.SAVE_FAILED,
                            "manifest.checkpoint_publish",
                        )

                    manifest_delayed: BaseException | None = None
                    try:
                        manifest_outcome = replace_manifest_atomically(
                            manifest_handle,
                            authority.handle,
                            expected_manifest,
                        )
                    except BaseException as error:
                        manifest_delayed = error
                        manifest_state = _resolve_atomic_destination(
                            authority.layout.manifest,
                            candidate_identity=manifest_handle.info.identity,
                            expected_identity=expected_manifest.info.identity,
                        )
                    else:
                        manifest_state = manifest_outcome.state
                        manifest_delayed = manifest_outcome.delayed_exception
                    if manifest_state is ManifestReplaceState.COMMITTED:
                        manifest_committed = True
                    elif manifest_state is ManifestReplaceState.UNKNOWN:
                        manifest_uncertain = True
                        if manifest_delayed is not None and not isinstance(
                            manifest_delayed, Exception
                        ):
                            raise _ManifestTransactionInterruption(
                                manifest_delayed,
                                preserve_recovery=True,
                            )
                        _reject(
                            ManifestRepositoryErrorCode.COMMIT_UNCERTAIN,
                            "manifest.commit_uncertain",
                        )
                    else:
                        if manifest_delayed is not None and not isinstance(
                            manifest_delayed, Exception
                        ):
                            raise _ManifestTransactionInterruption(
                                manifest_delayed,
                                preserve_recovery=True,
                            )
                        _reject(ManifestRepositoryErrorCode.SAVE_FAILED, "manifest.commit_replace")

                    # Release every retained writer/replacement handle before another local
                    # transaction can acquire the case lock. Reopen the authoritative record
                    # while that lock is still held so a successful save returns the exact
                    # state it committed rather than racing a subsequent save.
                    _close_handle(expected_checkpoint)
                    expected_checkpoint = None
                    _close_handle(expected_manifest)
                    expected_manifest = None
                    _close_handle(journal_handle)
                    journal_handle = None
                    _close_handle(manifest_handle)
                    manifest_handle = None
                    observed = _read_observed_manifest(authority)
                    if observed.validated.sha256 != candidate.sha256:
                        _reject(
                            ManifestRepositoryErrorCode.SAVE_FAILED,
                            "manifest.transaction_verify",
                        )
                    committed_snapshot = observed.snapshot
    except BaseException as error:
        failure = error
    finally:
        _close_handle(expected_checkpoint)
        _close_handle(expected_manifest)
        if journal_handle is not None:
            if not checkpoint_committed and not checkpoint_uncertain:
                _delete_handle(journal_handle)
            else:
                _close_handle(journal_handle)
        if manifest_handle is not None:
            if not manifest_committed and not manifest_uncertain:
                _delete_handle(manifest_handle)
            else:
                _close_handle(manifest_handle)

    if failure is not None:
        if isinstance(failure, ManifestRepositoryError):
            raise failure
        if isinstance(failure, _ManifestTransactionInterruption):
            raise failure
        if isinstance(failure, WorkspaceError):
            _map_workspace_error(failure, context="manifest.transaction_storage")
        if isinstance(failure, Exception):
            _reject(ManifestRepositoryErrorCode.SAVE_FAILED, "manifest.transaction_storage")
        raise _ManifestTransactionInterruption(
            failure,
            preserve_recovery=checkpoint_committed or checkpoint_uncertain,
        ) from None
    if not manifest_committed:
        _reject(ManifestRepositoryErrorCode.SAVE_FAILED, "manifest.transaction_result")
    if committed_snapshot is None:
        _reject(ManifestRepositoryErrorCode.SAVE_FAILED, "manifest.transaction_result")
    return committed_snapshot


def canonical_manifest_bytes(document: Mapping[str, object]) -> bytes:
    """Return deterministic manifest bytes after JSON-value and byte-limit checks."""

    return _canonical_manifest_bytes(document)


def validate_manifest_payload(payload: bytes, expected_case_id: UUID) -> None:
    """Validate a complete supported manifest for trusted storage adapters."""

    _validate_loadable_manifest_payload(payload, expected_case_id)


def _read_observed_manifest(
    authority: AuthenticatedCase,
    *,
    allow_legacy: bool = False,
) -> _ObservedManifest:
    raw = _read_raw_manifest(authority)
    return _ObservedManifest(
        (
            _validate_loadable_manifest_payload(raw.payload, authority.case_id)
            if allow_legacy
            else _validate_manifest_payload(raw.payload, authority.case_id)
        ),
        raw.identity,
    )


def _read_raw_manifest(authority: AuthenticatedCase) -> _RawManifest:
    path = authority.layout.manifest
    if not os.path.lexists(path):
        _reject(ManifestRepositoryErrorCode.MANIFEST_INVALID, "manifest.current_missing")
    handle: WindowsHandle | None = None
    try:
        handle = open_handle(path, read=True, share_delete=True)
        return _read_exact_handle(handle, path)
    except ManifestRepositoryError:
        raise
    except WorkspaceError as error:
        _map_workspace_error(error, context="manifest.current_read")
    finally:
        _close_handle(handle)


def _resolve_atomic_destination(
    destination: Path,
    *,
    candidate_identity: FileIdentity,
    expected_identity: FileIdentity | None,
) -> ManifestReplaceState:
    """Settle an interrupted publication from authenticated file identities only."""

    if not os.path.lexists(destination):
        return (
            ManifestReplaceState.NOT_COMMITTED
            if expected_identity is None
            else ManifestReplaceState.UNKNOWN
        )
    try:
        with open_handle(destination, share_delete=True) as current:
            require_expected_handle(current, destination, directory=False)
            if (
                current.info.is_reparse_point
                or current.info.is_sparse
                or current.info.link_count != 1
            ):
                return ManifestReplaceState.UNKNOWN
            require_no_alternate_streams(destination)
            if current.info.identity == candidate_identity:
                return ManifestReplaceState.COMMITTED
            if expected_identity is not None and current.info.identity == expected_identity:
                return ManifestReplaceState.NOT_COMMITTED
    except Exception:
        return ManifestReplaceState.UNKNOWN
    return ManifestReplaceState.UNKNOWN


def _read_checkpoint(authority: AuthenticatedCase) -> _ValidatedManifest | None:
    checkpoint_path = authority.layout.journal / _JOURNAL_CHECKPOINT_NAME
    try:
        with open_handle(authority.layout.journal) as journal_root:
            require_expected_handle(journal_root, authority.layout.journal, directory=True)
            verify_private_acl(journal_root)
            require_no_alternate_streams(authority.layout.journal)
            if not os.path.lexists(checkpoint_path):
                return None
            with open_handle(checkpoint_path, read=True, share_delete=True) as handle:
                raw = _read_exact_handle(handle, checkpoint_path)
        return _validate_loadable_manifest_payload(raw.payload, authority.case_id)
    except ManifestRepositoryError:
        raise
    except WorkspaceError as error:
        _map_workspace_error(error, context="manifest.checkpoint_read")


def _read_exact_handle(handle: WindowsHandle, path: Path) -> _RawManifest:
    require_expected_handle(handle, path, directory=False)
    if handle.info.is_reparse_point or handle.info.is_sparse or handle.info.link_count != 1:
        _reject(ManifestRepositoryErrorCode.MANIFEST_INVALID, "manifest.record_identity")
    require_no_alternate_streams(path)
    payload = handle.read_bytes(maximum_bytes=MAX_MANIFEST_BYTES)
    return _RawManifest(payload, handle.info.identity)


def _reopen_and_validate_stage(
    stage_handle: WindowsHandle,
    expected: _ValidatedManifest,
    case_id: UUID,
    *,
    allow_legacy: bool = False,
) -> None:
    with reopen_read_only_file(stage_handle) as staged:
        raw = _read_exact_handle(staged, staged.info.final_path)
    validated = (
        _validate_loadable_manifest_payload(raw.payload, case_id)
        if allow_legacy
        else _validate_manifest_payload(raw.payload, case_id)
    )
    if validated.sha256 != expected.sha256:
        _reject(ManifestRepositoryErrorCode.SAVE_FAILED, "manifest.stage_verify")


def _validate_manifest_payload(payload: bytes, expected_case_id: UUID) -> _ValidatedManifest:
    """Validate only the current writable manifest version."""

    validated = _validate_loadable_manifest_payload(payload, expected_case_id)
    if validated.schema_version != CURRENT_CASE_MANIFEST_VERSION:
        _reject(ManifestRepositoryErrorCode.VERSION_UNSUPPORTED, "manifest.validation_version")
    return validated


def _validate_loadable_manifest_payload(
    payload: bytes,
    expected_case_id: UUID,
) -> _ValidatedManifest:
    """Validate a frozen v1/v2 input or current v4 input without mutation."""

    selected = _require_case_id(expected_case_id, context="manifest.validation_case")
    if type(payload) is not bytes or not 1 <= len(payload) <= MAX_MANIFEST_BYTES:
        _reject(ManifestRepositoryErrorCode.MANIFEST_INVALID, "manifest.validation_bytes")
    try:
        supported_version = probe_supported_manifest_schema_version(payload)
    except Exception:
        _reject(ManifestRepositoryErrorCode.MANIFEST_INVALID, "manifest.validation_json")
    if supported_version is None:
        _reject(ManifestRepositoryErrorCode.VERSION_UNSUPPORTED, "manifest.validation_version")
    try:
        document = decode_manifest(payload)
    except Exception:
        _reject(ManifestRepositoryErrorCode.MANIFEST_INVALID, "manifest.validation_json")
    version = document.get("schema_version")
    if type(version) is not int:
        _reject(ManifestRepositoryErrorCode.MANIFEST_INVALID, "manifest.validation_version")
    if version != supported_version:
        _reject(ManifestRepositoryErrorCode.VERSION_UNSUPPORTED, "manifest.validation_version")
    issues = validate_manifest(document, _runtime_schema(version))
    if issues:
        code = issues[0].code
        if "VERSION" in code:
            _reject(ManifestRepositoryErrorCode.VERSION_UNSUPPORTED, "manifest.validation_version")
        if "CASE" in code and "MISMATCH" in code:
            _reject(ManifestRepositoryErrorCode.CASE_MISMATCH, "manifest.validation_case")
        _reject(ManifestRepositoryErrorCode.MANIFEST_INVALID, "manifest.validation_schema")
    try:
        summary = summary_from_manifest(document, selected)
    except Exception:
        raw_case_id = document.get("case_id")
        if type(raw_case_id) is str and raw_case_id != str(selected):
            _reject(ManifestRepositoryErrorCode.CASE_MISMATCH, "manifest.validation_case")
        _reject(ManifestRepositoryErrorCode.MANIFEST_INVALID, "manifest.validation_summary")
    return _ValidatedManifest(
        payload=payload,
        document=document,
        case_id=summary.case_id,
        schema_version=summary.manifest_version,
        revision=summary.revision,
        created_at=summary.created_at,
        updated_at=summary.updated_at,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


@lru_cache(maxsize=2)
def _runtime_schema(version: int = CURRENT_CASE_MANIFEST_VERSION) -> Mapping[str, object]:
    resource = _SCHEMA_RESOURCES.get(version)
    expected_fingerprint = _SCHEMA_FINGERPRINTS.get(version)
    if resource is None or expected_fingerprint is None:
        _reject(ManifestRepositoryErrorCode.VERSION_UNSUPPORTED, "manifest.schema_version")
    try:
        payload = files("paperworkshell.storage").joinpath(resource).read_bytes()
    except Exception:
        _reject(ManifestRepositoryErrorCode.STORAGE_FAILED, "manifest.schema_resource")
    if not 1 <= len(payload) <= _MAX_SCHEMA_BYTES:
        _reject(ManifestRepositoryErrorCode.STORAGE_FAILED, "manifest.schema_size")
    try:
        loaded = json.loads(payload.decode("utf-8"))
    except UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError:
        _reject(ManifestRepositoryErrorCode.STORAGE_FAILED, "manifest.schema_json")
    if type(loaded) is not dict:
        _reject(ManifestRepositoryErrorCode.STORAGE_FAILED, "manifest.schema_object")
    schema = cast(dict[str, object], loaded)
    fingerprint = hashlib.sha256(
        json.dumps(
            schema,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    if fingerprint != expected_fingerprint:
        _reject(ManifestRepositoryErrorCode.STORAGE_FAILED, "manifest.schema_identity")
    return schema


def _canonical_manifest_bytes(document: Mapping[str, object]) -> bytes:
    if not isinstance(document, Mapping):
        _reject(ManifestRepositoryErrorCode.MODEL_INVALID, "manifest.document")
    try:
        payload = (
            json.dumps(
                dict(document),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
        )
    except TypeError, ValueError, OverflowError, RecursionError:
        _reject(ManifestRepositoryErrorCode.MANIFEST_INVALID, "manifest.document_json")
    if len(payload) > MAX_MANIFEST_BYTES:
        _reject(ManifestRepositoryErrorCode.CAPACITY_EXCEEDED, "manifest.document_bytes")
    return payload


def _cas_token(identity: FileIdentity, digest: str) -> str:
    material = (
        identity.volume_serial_number.to_bytes(8, byteorder="big", signed=False)
        + identity.file_id
        + bytes.fromhex(digest)
    )
    return hashlib.sha256(material).hexdigest()


def _parse_manifest_timestamp(value: object) -> datetime:
    try:
        return parse_utc_timestamp(value)
    except Exception:
        _reject(ManifestRepositoryErrorCode.MANIFEST_INVALID, "manifest.timestamp")


def _require_expected_save_tokens(revision: object, digest: object, token: object) -> None:
    if (
        type(revision) is not int
        or not 0 <= revision <= MAX_SAFE_MANIFEST_INTEGER
        or type(digest) is not str
        or len(digest) != _SHA256_LENGTH
        or type(token) is not str
        or len(token) != _SHA256_LENGTH
    ):
        _reject(ManifestRepositoryErrorCode.MODEL_INVALID, "manifest.expected")
    try:
        bytes.fromhex(digest)
        bytes.fromhex(token)
    except ValueError:
        _reject(ManifestRepositoryErrorCode.MODEL_INVALID, "manifest.expected")


def _require_case_id(value: object, *, context: str) -> UUID:
    if type(value) is not UUID or value.version != 4:
        _reject(ManifestRepositoryErrorCode.MODEL_INVALID, context)
    return value


def _next_internal_id(factory: Callable[[], UUID], *, context: str) -> UUID:
    try:
        value = factory()
    except BaseException:
        _reject(ManifestRepositoryErrorCode.STORAGE_FAILED, context)
    return _require_case_id(value, context=context)


def _map_workspace_error(error: WorkspaceError, *, context: str) -> Never:
    if error.code is WorkspaceErrorCode.QUOTA_EXCEEDED:
        _reject(ManifestRepositoryErrorCode.CAPACITY_EXCEEDED, context)
    if error.code in {
        WorkspaceErrorCode.ALIAS_DETECTED,
        WorkspaceErrorCode.REPARSE_POINT,
        WorkspaceErrorCode.IDENTITY_CHANGED,
        WorkspaceErrorCode.PERMISSIONS_INSECURE,
    }:
        _reject(ManifestRepositoryErrorCode.MANIFEST_INVALID, context)
    _reject(ManifestRepositoryErrorCode.STORAGE_FAILED, context)


def _delete_handle(handle: WindowsHandle) -> None:
    try:
        handle.mark_for_deletion()
    except BaseException:
        pass
    _close_handle(handle)


def _close_handle(handle: WindowsHandle | None) -> None:
    if handle is None:
        return
    try:
        handle.close()
    except BaseException:
        pass


def _reject(code: ManifestRepositoryErrorCode, context: str) -> Never:
    raise ManifestRepositoryError(code=code, context=context)

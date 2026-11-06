"""Bounded create-only retention of exact pre-migration case-record bytes."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Final
from uuid import UUID, uuid4

from paperworkshell.domain.manifest_repository import (
    ManifestRepositoryError,
    ManifestRepositoryErrorCode,
    reject_manifest_repository,
)
from paperworkshell.storage._windows import (
    WindowsHandle,
    open_handle,
    rename_open_file_no_replace_at,
    reopen_read_only_file,
    require_expected_handle,
    require_no_alternate_streams,
    verify_private_acl,
    write_new_file,
)
from paperworkshell.storage.errors import WorkspaceError, WorkspaceErrorCode
from paperworkshell.storage.manifest_codec import MAX_MANIFEST_BYTES
from paperworkshell.storage.quotas import QuotaArea
from paperworkshell.storage.workspace import AuthenticatedCase, CaseWorkspaceManager

MIGRATION_BACKUP_NAMES: Final[Mapping[int, str]] = MappingProxyType(
    {1: "migration-source-v1.json", 2: "migration-source-v2.json"}
)


def retain_migration_source(
    manager: CaseWorkspaceManager,
    authority: AuthenticatedCase,
    *,
    payload: bytes,
    schema_version: int,
    id_factory: Callable[[], UUID] = uuid4,
) -> None:
    """Retain one validated old record before its first migration checkpoint.

    The caller validates the complete source under its case transaction. Only
    the two fixed names can be created; an existing exact copy is idempotent,
    while any different/unsafe object fails closed and is never replaced.
    """

    if (
        type(schema_version) is not int
        or schema_version not in MIGRATION_BACKUP_NAMES
        or type(payload) is not bytes
        or not 1 <= len(payload) <= MAX_MANIFEST_BYTES
        or not callable(id_factory)
    ):
        reject_manifest_repository(
            ManifestRepositoryErrorCode.MODEL_INVALID, "manifest.migration_backup"
        )
    stage: WindowsHandle | None = None
    stage_path: Path | None = None
    try:
        with authority.transaction_guard():
            with (
                open_handle(authority.layout.case_root, traverse=True) as case_root,
                open_handle(authority.layout.journal, traverse=True) as journal,
                open_handle(authority.layout.temp, traverse=True) as temporary,
            ):
                require_expected_handle(
                    case_root,
                    authority.layout.case_root,
                    directory=True,
                    expected_identity=authority.identity,
                )
                for handle, path in (
                    (case_root, authority.layout.case_root),
                    (journal, authority.layout.journal),
                    (temporary, authority.layout.temp),
                ):
                    require_expected_handle(handle, path, directory=True)
                    verify_private_acl(handle)
                    require_no_alternate_streams(path)
                name = MIGRATION_BACKUP_NAMES[schema_version]
                destination = authority.layout.journal / name
                if os.path.lexists(destination):
                    _verify_backup(destination, payload)
                    return
                stage_id = id_factory()
                if type(stage_id) is not UUID or stage_id.version != 4:
                    reject_manifest_repository(
                        ManifestRepositoryErrorCode.MODEL_INVALID,
                        "manifest.migration_backup_id",
                    )
                stage_path = authority.layout.temp / f"{stage_id}.tmp"
                with manager.reserve_capacity(
                    authority.case_id,
                    QuotaArea.TEMP,
                    relative_path=f"temp/{stage_id}.tmp",
                    is_directory=False,
                    byte_count=len(payload),
                ) as reservation:
                    with reservation.publication_guard(
                        case_identity=authority.identity,
                        relative_parts=reservation.relative_parts,
                        is_directory=False,
                        byte_count=len(payload),
                        entry_count=1,
                    ):
                        stage = write_new_file(stage_path, payload)
                        with reopen_read_only_file(stage) as reader:
                            _verify_handle(reader, stage_path, payload)
                        renamed, _error_number = rename_open_file_no_replace_at(
                            stage, journal, destination_name=name
                        )
                        if not renamed:
                            reject_manifest_repository(
                                ManifestRepositoryErrorCode.SAVE_FAILED,
                                "manifest.migration_backup_publish",
                            )
                        stage.refresh_info()
                        require_expected_handle(stage, destination, directory=False)
                        stage.close()
                        stage = None
                        _verify_backup(destination, payload)
    except ManifestRepositoryError:
        raise
    except WorkspaceError as error:
        code = (
            ManifestRepositoryErrorCode.CAPACITY_EXCEEDED
            if error.code is WorkspaceErrorCode.QUOTA_EXCEEDED
            else ManifestRepositoryErrorCode.STORAGE_FAILED
        )
        raise ManifestRepositoryError(code, "manifest.migration_backup_storage") from None
    except Exception:
        raise ManifestRepositoryError(
            ManifestRepositoryErrorCode.SAVE_FAILED, "manifest.migration_backup_storage"
        ) from None
    finally:
        if stage is not None:
            try:
                # An interruption after rename may leave a published backup.
                # Delete only an authenticated handle still at its random stage.
                if stage_path is not None and stage.refresh_info().final_path == stage_path:
                    stage.mark_for_deletion()
            except BaseException:
                pass
            finally:
                try:
                    stage.close()
                except BaseException:
                    pass


def _verify_backup(path: Path, expected: bytes) -> None:
    with open_handle(path, read=True, share_delete=True) as handle:
        _verify_handle(handle, path, expected)


def _verify_handle(handle: WindowsHandle, path: Path, expected: bytes) -> None:
    handle.refresh_info()
    require_expected_handle(handle, path, directory=False)
    if handle.info.is_reparse_point or handle.info.is_sparse or handle.info.link_count != 1:
        reject_manifest_repository(
            ManifestRepositoryErrorCode.MANIFEST_INVALID, "manifest.migration_backup_identity"
        )
    require_no_alternate_streams(path)
    if handle.read_bytes(maximum_bytes=MAX_MANIFEST_BYTES) != expected:
        reject_manifest_repository(
            ManifestRepositoryErrorCode.RECOVERY_FAILED, "manifest.migration_backup_conflict"
        )

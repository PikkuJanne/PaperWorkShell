"""Authenticated Windows startup recovery for case-owned persistent state."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast
from uuid import UUID

from paperworkshell.domain.manifest_repository import (
    ManifestLoadStatus,
    ManifestRepositoryError,
    ManifestRepositoryErrorCode,
    ManifestSnapshot,
)
from paperworkshell.domain.pdf_thumbnails import PdfThumbnailError
from paperworkshell.domain.recovery import (
    CaseRecoveryResult,
    CaseRecoveryStatus,
    ExportHistoryRecoveryResult,
    RecoveryErrorCode,
    RecoveryIssueCode,
    StartupRecoveryResult,
    reject_recovery,
)
from paperworkshell.storage._windows import (
    FileIdentity,
    WindowsHandle,
    open_handle,
    require_expected_handle,
    require_no_alternate_streams,
    verify_private_acl,
)
from paperworkshell.storage.errors import WorkspaceError, WorkspaceErrorCode, reject
from paperworkshell.storage.manifest_codec import MAX_MANIFEST_BYTES
from paperworkshell.storage.manifest_repository import (
    WindowsManifestRepository,
    validate_manifest_payload,
)
from paperworkshell.storage.thumbnail_cache import (
    ThumbnailCleanupStatus,
    WindowsThumbnailCacheStore,
)
from paperworkshell.storage.workspace import (
    AuthenticatedCase,
    CaseWorkspaceManager,
    CleanupStatus,
    CleanupTarget,
)

_MAXIMUM_SOURCE_ENTRIES: Final = 4_096
_MAXIMUM_CASE_SETTLEMENT_ATTEMPTS: Final = 2
_SOURCE_STORAGE_NAME: Final = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.bin\Z"
)


class _ManifestChanged(RuntimeError):
    """Private control signal; no filesystem value is retained."""


@dataclass(frozen=True, slots=True)
class _SourceCleanupReport:
    removed_entries: int = 0
    retained_entries: int = 0
    released_bytes: int = 0


@dataclass(frozen=True, slots=True)
class _CaseCleanupReport:
    source: _SourceCleanupReport
    thumbnail_removed: int
    thumbnail_released_bytes: int
    temp_files_removed: int
    temp_directories_removed: int
    temp_released_bytes: int
    issues: tuple[RecoveryIssueCode, ...]


class WindowsStartupRecoveryStore:
    """Recover valid manifests first, then clean only authenticated disposable state."""

    __slots__ = ("_manager", "_manifests", "_settle_exports", "_thumbnails")

    def __init__(
        self,
        manager: CaseWorkspaceManager,
        *,
        settle_exports: Callable[[UUID], ExportHistoryRecoveryResult] | None = None,
    ) -> None:
        if not isinstance(manager, CaseWorkspaceManager) or (
            settle_exports is not None and not callable(settle_exports)
        ):
            reject_recovery(RecoveryErrorCode.MODEL_INVALID, "recovery.store")
        self._manager = manager
        self._manifests = WindowsManifestRepository(manager)
        self._thumbnails = WindowsThumbnailCacheStore(manager)
        self._settle_exports = settle_exports

    def scan_and_recover(self) -> StartupRecoveryResult:
        """Return one deterministic result while leaving invalid cases untouched."""

        try:
            case_ids = self._manager.enumerate_case_ids()
        except WorkspaceError:
            reject_recovery(RecoveryErrorCode.SCAN_FAILED, "recovery.enumerate")
        results = tuple(self._recover_case(case_id) for case_id in case_ids)
        return StartupRecoveryResult(results)

    def _recover_case(self, case_id: UUID) -> CaseRecoveryResult:
        recovered_manifest = False
        export_recovery = ExportHistoryRecoveryResult()
        exports_settled = False
        loaded: ManifestSnapshot | None = None
        for attempt in range(_MAXIMUM_CASE_SETTLEMENT_ATTEMPTS):
            try:
                load_result = self._manifests.load(case_id)
            except ManifestRepositoryError as error:
                return _unavailable_case(case_id, _manifest_issue(error.code))
            except Exception:
                return _unavailable_case(
                    case_id,
                    RecoveryIssueCode.MANIFEST_RECOVERY_FAILED,
                )
            loaded = load_result.snapshot
            recovered_manifest = (
                recovered_manifest or load_result.status is ManifestLoadStatus.RECOVERED
            )
            if not exports_settled:
                export_recovery = self._recover_exports(case_id)
                exports_settled = True
            try:
                cleanup = self._cleanup_loaded_case(loaded)
            except _ManifestChanged:
                if attempt + 1 < _MAXIMUM_CASE_SETTLEMENT_ATTEMPTS:
                    continue
                return CaseRecoveryResult(
                    case_id=case_id,
                    status=(
                        CaseRecoveryStatus.RECOVERED
                        if recovered_manifest or export_recovery.repaired_entries
                        else CaseRecoveryStatus.CURRENT
                    ),
                    manifest_revision=loaded.revision,
                    issues=tuple(
                        sorted(
                            {
                                RecoveryIssueCode.CASE_CHANGED,
                                *export_recovery.issues,
                            },
                            key=lambda issue: issue.value,
                        )
                    ),
                    repaired_export_history=export_recovery.repaired_entries,
                    pending_export_history=export_recovery.pending_entries,
                    ambiguous_export_commits=export_recovery.ambiguous_entries,
                    invalid_export_retries=export_recovery.invalid_entries,
                )
            except Exception:
                return CaseRecoveryResult(
                    case_id=case_id,
                    status=(
                        CaseRecoveryStatus.RECOVERED
                        if recovered_manifest or export_recovery.repaired_entries
                        else CaseRecoveryStatus.CURRENT
                    ),
                    manifest_revision=loaded.revision,
                    issues=tuple(
                        sorted(
                            {
                                RecoveryIssueCode.CASE_CHANGED,
                                *export_recovery.issues,
                            },
                            key=lambda issue: issue.value,
                        )
                    ),
                    repaired_export_history=export_recovery.repaired_entries,
                    pending_export_history=export_recovery.pending_entries,
                    ambiguous_export_commits=export_recovery.ambiguous_entries,
                    invalid_export_retries=export_recovery.invalid_entries,
                )
            return CaseRecoveryResult(
                case_id=case_id,
                status=(
                    CaseRecoveryStatus.RECOVERED
                    if recovered_manifest or export_recovery.repaired_entries
                    else CaseRecoveryStatus.CURRENT
                ),
                manifest_revision=loaded.revision,
                issues=tuple(
                    sorted(
                        {*cleanup.issues, *export_recovery.issues},
                        key=lambda issue: issue.value,
                    )
                ),
                removed_source_orphans=cleanup.source.removed_entries,
                retained_source_artifacts=cleanup.source.retained_entries,
                removed_thumbnail_stages=cleanup.thumbnail_removed,
                removed_temp_files=cleanup.temp_files_removed,
                removed_temp_directories=cleanup.temp_directories_removed,
                repaired_export_history=export_recovery.repaired_entries,
                pending_export_history=export_recovery.pending_entries,
                ambiguous_export_commits=export_recovery.ambiguous_entries,
                invalid_export_retries=export_recovery.invalid_entries,
                released_bytes=(
                    cleanup.source.released_bytes
                    + cleanup.thumbnail_released_bytes
                    + cleanup.temp_released_bytes
                ),
            )
        if loaded is None:
            return _unavailable_case(case_id, RecoveryIssueCode.MANIFEST_RECOVERY_FAILED)
        return CaseRecoveryResult(
            case_id=case_id,
            status=(
                CaseRecoveryStatus.RECOVERED
                if export_recovery.repaired_entries
                else CaseRecoveryStatus.CURRENT
            ),
            manifest_revision=loaded.revision,
            issues=tuple(
                sorted(
                    {RecoveryIssueCode.CASE_CHANGED, *export_recovery.issues},
                    key=lambda issue: issue.value,
                )
            ),
            repaired_export_history=export_recovery.repaired_entries,
            pending_export_history=export_recovery.pending_entries,
            ambiguous_export_commits=export_recovery.ambiguous_entries,
            invalid_export_retries=export_recovery.invalid_entries,
        )

    def _recover_exports(self, case_id: UUID) -> ExportHistoryRecoveryResult:
        if self._settle_exports is None:
            return ExportHistoryRecoveryResult()
        try:
            result = self._settle_exports(case_id)
        except Exception:
            return ExportHistoryRecoveryResult(invalid_entries=1)
        if type(result) is not ExportHistoryRecoveryResult:
            return ExportHistoryRecoveryResult(invalid_entries=1)
        return result

    def _cleanup_loaded_case(self, snapshot: ManifestSnapshot) -> _CaseCleanupReport:
        issues: list[RecoveryIssueCode] = []
        source = _SourceCleanupReport()
        thumbnail_removed = 0
        thumbnail_released = 0
        temp_files_removed = 0
        temp_directories_removed = 0
        temp_released = 0

        with self._manager.authenticate_case(snapshot.case_id) as authority:
            with authority.transaction_guard():
                _require_current_snapshot(authority, snapshot)
                try:
                    source = _cleanup_source_orphans(authority, snapshot)
                except WorkspaceError:
                    issues.append(RecoveryIssueCode.SOURCE_ARTIFACTS_RETAINED)
                else:
                    if source.retained_entries:
                        issues.append(RecoveryIssueCode.SOURCE_ARTIFACTS_RETAINED)

                try:
                    thumbnail = self._thumbnails.cleanup_stale_stages(snapshot.case_id)
                except PdfThumbnailError, WorkspaceError:
                    issues.append(RecoveryIssueCode.THUMBNAIL_CLEANUP_PARTIAL)
                else:
                    thumbnail_removed = thumbnail.removed_entries
                    thumbnail_released = thumbnail.released_bytes
                    if thumbnail.status is ThumbnailCleanupStatus.PARTIAL:
                        issues.append(RecoveryIssueCode.THUMBNAIL_CLEANUP_PARTIAL)

                try:
                    temporary = self._manager.cleanup_disposable(
                        snapshot.case_id,
                        CleanupTarget.TEMP,
                    )
                except WorkspaceError:
                    issues.append(RecoveryIssueCode.TEMP_CLEANUP_PARTIAL)
                else:
                    temp_files_removed = temporary.removed_files
                    temp_directories_removed = temporary.removed_directories
                    temp_released = temporary.released_bytes
                    if temporary.status is CleanupStatus.PARTIAL:
                        issues.append(RecoveryIssueCode.TEMP_CLEANUP_PARTIAL)

        return _CaseCleanupReport(
            source=source,
            thumbnail_removed=thumbnail_removed,
            thumbnail_released_bytes=thumbnail_released,
            temp_files_removed=temp_files_removed,
            temp_directories_removed=temp_directories_removed,
            temp_released_bytes=temp_released,
            issues=tuple(issues),
        )


def _unavailable_case(case_id: UUID, issue: RecoveryIssueCode) -> CaseRecoveryResult:
    return CaseRecoveryResult(
        case_id=case_id,
        status=CaseRecoveryStatus.UNAVAILABLE,
        manifest_revision=None,
        issues=(issue,),
    )


def _manifest_issue(code: ManifestRepositoryErrorCode) -> RecoveryIssueCode:
    if code is ManifestRepositoryErrorCode.VERSION_UNSUPPORTED:
        return RecoveryIssueCode.MANIFEST_VERSION_UNSUPPORTED
    if code in {
        ManifestRepositoryErrorCode.MANIFEST_INVALID,
        ManifestRepositoryErrorCode.CASE_MISMATCH,
    }:
        return RecoveryIssueCode.MANIFEST_INVALID
    return RecoveryIssueCode.MANIFEST_RECOVERY_FAILED


def _require_current_snapshot(
    authority: AuthenticatedCase,
    snapshot: ManifestSnapshot,
) -> None:
    path = authority.layout.manifest
    try:
        with open_handle(path, read=True, share_delete=True) as handle:
            require_expected_handle(handle, path, directory=False)
            initial = handle.info
            if initial.is_reparse_point or initial.is_sparse or initial.link_count != 1:
                raise _ManifestChanged from None
            require_no_alternate_streams(path)
            payload = handle.read_bytes(maximum_bytes=MAX_MANIFEST_BYTES)
            current = handle.refresh_info()
            require_expected_handle(handle, path, directory=False)
            if (
                current.identity != initial.identity
                or current.logical_size != initial.logical_size
                or current.is_reparse_point
                or current.is_sparse
                or current.link_count != 1
            ):
                raise _ManifestChanged from None
            require_no_alternate_streams(path)
        validate_manifest_payload(payload, authority.case_id)
        digest = hashlib.sha256(payload).hexdigest()
        if not hmac.compare_digest(digest, snapshot.sha256) or not hmac.compare_digest(
            _manifest_cas_token(current.identity, digest),
            snapshot.cas_token,
        ):
            raise _ManifestChanged from None
    except _ManifestChanged:
        raise
    except Exception:
        raise _ManifestChanged from None


def _cleanup_source_orphans(
    authority: AuthenticatedCase,
    snapshot: ManifestSnapshot,
) -> _SourceCleanupReport:
    expected = _manifest_source_names(snapshot)
    root_path = authority.layout.originals
    with open_handle(root_path) as root:
        require_expected_handle(root, root_path, directory=True)
        verify_private_acl(root)
        require_no_alternate_streams(root_path)
        names = _bounded_names(root_path)
        candidates = [
            name
            for name in names
            if name not in expected and _SOURCE_STORAGE_NAME.fullmatch(name) is not None
        ]
        retained = sum(
            name not in expected and _SOURCE_STORAGE_NAME.fullmatch(name) is None for name in names
        )
        removed = 0
        released = 0
        for name in candidates:
            try:
                was_removed, byte_count = _delete_exact_source(root_path / name, root)
            except WorkspaceError:
                retained += 1
                continue
            if was_removed:
                removed += 1
                released += byte_count
        require_expected_handle(root, root_path, directory=True)
        verify_private_acl(root)
        require_no_alternate_streams(root_path)
    return _SourceCleanupReport(removed, retained, released)


def _manifest_source_names(snapshot: ManifestSnapshot) -> frozenset[str]:
    document = snapshot.document
    raw_sources = document.get("sources")
    if type(raw_sources) is not list:
        reject_recovery(RecoveryErrorCode.INTERNAL_ERROR, "recovery.manifest_sources")
    names: set[str] = set()
    for raw_source in cast(list[object], raw_sources):
        if type(raw_source) is not dict:
            reject_recovery(RecoveryErrorCode.INTERNAL_ERROR, "recovery.manifest_source")
        storage_name = cast(dict[str, object], raw_source).get("storage_name")
        if (
            type(storage_name) is not str
            or _SOURCE_STORAGE_NAME.fullmatch(storage_name) is None
            or storage_name in names
        ):
            reject_recovery(RecoveryErrorCode.INTERNAL_ERROR, "recovery.manifest_source")
        names.add(storage_name)
    return frozenset(names)


def _bounded_names(path: Path) -> tuple[str, ...]:
    try:
        names: list[str] = []
        with os.scandir(path) as iterator:
            for entry in iterator:
                if len(names) >= _MAXIMUM_SOURCE_ENTRIES:
                    reject(
                        WorkspaceErrorCode.SCAN_LIMIT_EXCEEDED,
                        "recovery.source_entries",
                        "Source recovery exceeded its configured entry limit.",
                    )
                names.append(entry.name)
    except WorkspaceError:
        raise
    except OSError:
        reject(
            WorkspaceErrorCode.IDENTITY_CHANGED,
            "recovery.source_scan",
            "Source recovery could not authenticate the case inventory.",
        )
    return tuple(sorted(names, key=str.casefold))


def _delete_exact_source(path: Path, root: WindowsHandle) -> tuple[bool, int]:
    if not os.path.lexists(path):
        return False, 0
    with open_handle(path, delete=True) as handle:
        require_expected_handle(root, path.parent, directory=True)
        require_expected_handle(handle, path, directory=False)
        initial = handle.info
        if initial.is_reparse_point or initial.is_sparse or initial.link_count != 1:
            reject(
                WorkspaceErrorCode.ALIAS_DETECTED,
                "recovery.source_identity",
                "An orphan source candidate could not be authenticated safely.",
            )
        require_no_alternate_streams(path)
        current = handle.refresh_info()
        require_expected_handle(root, path.parent, directory=True)
        require_expected_handle(handle, path, directory=False)
        if (
            current.identity != initial.identity
            or current.logical_size != initial.logical_size
            or current.is_reparse_point
            or current.is_sparse
            or current.link_count != 1
        ):
            reject(
                WorkspaceErrorCode.IDENTITY_CHANGED,
                "recovery.source_changed",
                "An orphan source candidate changed before cleanup.",
            )
        require_no_alternate_streams(path)
        handle.mark_for_deletion()
        return True, current.logical_size


def _manifest_cas_token(identity: FileIdentity, digest: str) -> str:
    material = (
        identity.volume_serial_number.to_bytes(8, byteorder="big", signed=False)
        + identity.file_id
        + bytes.fromhex(digest)
    )
    return hashlib.sha256(material).hexdigest()

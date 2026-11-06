"""Current-user uninstall review reuses authenticated, no-follow case removal."""

from __future__ import annotations

import ctypes
import hashlib
import hmac
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from ctypes import wintypes
from typing import Final

from paperworkshell.domain.resource_policy import select_resource_policy
from paperworkshell.domain.uninstall_review import (
    UninstallCaseInventory,
    UninstallCaseReference,
    UninstallReviewError,
    UninstallReviewErrorCode,
)
from paperworkshell.storage._windows import FileIdentity
from paperworkshell.storage.case_lifecycle import _validate_case_manifest_payload
from paperworkshell.storage.manifest_codec import MAX_MANIFEST_BYTES
from paperworkshell.storage.manifest_repository import _read_raw_manifest
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.workspace import CaseRemovalStatus, CaseWorkspaceManager

APPLICATION_MUTEX: Final = r"Local\io.github.pikkujanne.paperworkshell.App"


@contextmanager
def uninstall_application_guard() -> Iterator[None]:
    """Exclude packaged launches throughout review and deletion in this session.

    The normal native launcher holds the same mutex for its Python child's
    lifetime. The native review launcher leaves acquisition to this helper.
    Direct development launches are not a packaged lifecycle mechanism.
    """
    handle: int | None = None
    try:
        library = ctypes.WinDLL("kernel32", use_last_error=True)
        create = library.CreateMutexW
        create.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        create.restype = wintypes.HANDLE
        close = library.CloseHandle
        close.argtypes = (wintypes.HANDLE,)
        close.restype = wintypes.BOOL
        release = library.ReleaseMutex
        release.argtypes = (wintypes.HANDLE,)
        release.restype = wintypes.BOOL
        ctypes.set_last_error(0)
        raw = create(None, True, APPLICATION_MUTEX)
        error = ctypes.get_last_error()
        handle = int(raw) if raw else None
        if handle is None:
            raise UninstallReviewError(UninstallReviewErrorCode.INVALID)
        if error == 183:  # ERROR_ALREADY_EXISTS: initial ownership was ignored.
            close(wintypes.HANDLE(handle))
            handle = None
            raise UninstallReviewError(UninstallReviewErrorCode.APPLICATION_RUNNING)
    except UninstallReviewError:
        raise
    except Exception:
        raise UninstallReviewError(UninstallReviewErrorCode.INVALID) from None
    try:
        yield
    finally:
        if handle is not None:
            released = release(wintypes.HANDLE(handle))
            closed = close(wintypes.HANDLE(handle))
            if not released or not closed:
                raise UninstallReviewError(UninstallReviewErrorCode.INVALID)


def _identity_digest(identity: FileIdentity) -> str:
    return hashlib.sha256(
        identity.volume_serial_number.to_bytes(8, "little") + identity.file_id
    ).hexdigest()


class WindowsUninstallCaseStore:
    """Handle one bounded manifest at a time; never follow external document paths."""

    def __init__(self, manager: CaseWorkspaceManager) -> None:
        self._manager = manager

    def inspect(self, *, cancellation: Callable[[], bool]) -> UninstallCaseInventory:
        cases: list[UninstallCaseReference] = []
        unavailable = 0
        for case_id in self._manager.enumerate_case_ids():
            if cancellation():
                raise UninstallReviewError(UninstallReviewErrorCode.CANCELLED)
            try:
                with self._manager.authenticate_case(case_id) as authority:
                    raw = _read_raw_manifest(authority)
                    _validate_case_manifest_payload(raw.payload, case_id)
                    cases.append(
                        UninstallCaseReference(
                            case_id,
                            hashlib.sha256(raw.payload).hexdigest(),
                            _identity_digest(authority.identity),
                        )
                    )
            except Exception:
                unavailable += 1
        return UninstallCaseInventory(tuple(cases), unavailable)

    def delete(self, case: UninstallCaseReference) -> bool:
        if type(case) is not UninstallCaseReference:
            raise UninstallReviewError(UninstallReviewErrorCode.INVALID)

        # Validate the exact reviewed bytes while holding deletion authority,
        # before manifest-first removal. Inspection never runs migration or
        # journal recovery; unsupported cases remain untouched.
        def validate(payload: bytes) -> None:
            _validate_case_manifest_payload(payload, case.case_id)
            if not hmac.compare_digest(hashlib.sha256(payload).hexdigest(), case.manifest_sha256):
                raise UninstallReviewError(UninstallReviewErrorCode.REVIEW_STALE)

        with self._manager.authenticate_case(case.case_id, for_deletion=True) as authority:
            if not hmac.compare_digest(
                _identity_digest(authority.identity), case.case_identity_sha256
            ):
                raise UninstallReviewError(UninstallReviewErrorCode.REVIEW_STALE)
            result = self._manager.remove_case(
                authority,
                manifest_validator=validate,
                maximum_manifest_bytes=MAX_MANIFEST_BYTES,
            )
        return result.status is CaseRemovalStatus.COMPLETE


def current_user_uninstall_store() -> WindowsUninstallCaseStore:
    """Resolve only the OS-authenticated production root, never a command-line path."""
    policy = select_resource_policy()
    quota = WorkspaceQuota(
        maximum_case_bytes=policy.maximum_case_bytes,
        maximum_cache_bytes=policy.maximum_cache_bytes,
        maximum_temp_bytes=policy.maximum_temporary_storage_bytes,
        maximum_entries=50_000,
        maximum_depth=32,
    )
    return WindowsUninstallCaseStore(
        CaseWorkspaceManager.for_current_user(
            quota=quota,
            minimum_free_space_reserve_bytes=policy.minimum_export_free_space_reserve_bytes,
        )
    )

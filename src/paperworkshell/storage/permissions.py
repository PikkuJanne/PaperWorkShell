"""Restrictive case-directory permission application and verification."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from paperworkshell.storage._windows import (
    FileIdentity,
    WindowsHandle,
    open_handle,
    require_expected_handle,
    require_no_alternate_streams,
    verify_private_acl,
)
from paperworkshell.storage.errors import WorkspaceError, WorkspaceErrorCode, reject


@dataclass(frozen=True, slots=True)
class PermissionVerification:
    """Evidence that one app-created directory passed the supported policy."""

    identity: FileIdentity
    mechanism: str = "windows_protected_acl_v1"


class PermissionAdapter(Protocol):
    """Boundary used by workspace provisioning to apply and verify permissions."""

    def create_private_directory(self, path: Path, *, context: str) -> PermissionVerification:
        """Create exactly one private directory and independently verify it."""

    def verify_private_directory(self, path: Path, *, context: str) -> PermissionVerification:
        """Verify one existing private directory without following links."""


class WindowsPrivateDirectoryPermissions:
    """CPython 0o700 creation plus independent opened-handle ACL verification."""

    def create_private_directory(self, path: Path, *, context: str) -> PermissionVerification:
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            reject(
                WorkspaceErrorCode.ALREADY_EXISTS,
                context,
                "The generated private directory already exists.",
            )
        except OSError:
            reject(
                WorkspaceErrorCode.CREATE_FAILED,
                context,
                "PaperWorkShell could not create a private case directory.",
            )

        try:
            with open_handle(path, delete=True) as handle:
                try:
                    return self._verify_opened_directory(handle, path)
                except BaseException:
                    self._remove_failed_empty_directory(handle)
                    raise
        except WorkspaceError:
            raise
        except OSError:
            reject(
                WorkspaceErrorCode.PERMISSIONS_UNSUPPORTED,
                context,
                "PaperWorkShell could not verify private storage permissions.",
            )

    def verify_private_directory(self, path: Path, *, context: str) -> PermissionVerification:
        try:
            with open_handle(path) as handle:
                return self._verify_opened_directory(handle, path)
        except WorkspaceError:
            raise
        except OSError:
            reject(
                WorkspaceErrorCode.PERMISSIONS_UNSUPPORTED,
                context,
                "PaperWorkShell could not verify private storage permissions.",
            )

    @staticmethod
    def _verify_opened_directory(
        handle: WindowsHandle,
        path: Path,
    ) -> PermissionVerification:
        require_expected_handle(handle, path, directory=True)
        verify_private_acl(handle)
        require_no_alternate_streams(path)
        return PermissionVerification(identity=handle.info.identity)

    @staticmethod
    def _remove_failed_empty_directory(handle: WindowsHandle) -> None:
        try:
            handle.mark_for_deletion()
        except OSError, WorkspaceError:
            # The original verification error remains authoritative. The random failed leaf is
            # never returned as a usable workspace and contains no user document bytes.
            return

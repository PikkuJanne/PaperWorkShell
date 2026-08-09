"""Stable, non-disclosing storage failures."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Never


class WorkspaceErrorCode(StrEnum):
    """Closed error-code registry for case-workspace operations."""

    PATH_INVALID = "WORKSPACE_PATH_INVALID"
    LOCATION_UNSUPPORTED = "WORKSPACE_LOCATION_UNSUPPORTED"
    CASE_ID_INVALID = "WORKSPACE_CASE_ID_INVALID"
    NAME_INVALID = "WORKSPACE_NAME_INVALID"
    ALREADY_EXISTS = "WORKSPACE_ALREADY_EXISTS"
    CREATE_FAILED = "WORKSPACE_CREATE_FAILED"
    REPARSE_POINT = "WORKSPACE_REPARSE_POINT"
    IDENTITY_CHANGED = "WORKSPACE_IDENTITY_CHANGED"
    ALIAS_DETECTED = "WORKSPACE_ALIAS_DETECTED"
    PERMISSIONS_UNSUPPORTED = "WORKSPACE_PERMISSIONS_UNSUPPORTED"
    PERMISSIONS_INSECURE = "WORKSPACE_PERMISSIONS_INSECURE"
    QUOTA_INVALID = "WORKSPACE_QUOTA_INVALID"
    QUOTA_EXCEEDED = "WORKSPACE_QUOTA_EXCEEDED"
    SCAN_LIMIT_EXCEEDED = "WORKSPACE_SCAN_LIMIT_EXCEEDED"
    CLEANUP_FAILED = "WORKSPACE_CLEANUP_FAILED"


@dataclass(slots=True)
class WorkspaceError(ValueError):
    """A storage error containing only a stable code and safe context token."""

    code: WorkspaceErrorCode
    context: str
    safe_message: str

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject(
    code: WorkspaceErrorCode,
    context: str,
    safe_message: str,
) -> Never:
    """Raise a sanitized workspace failure without including a path or input value."""

    raise WorkspaceError(code=code, context=context, safe_message=safe_message)

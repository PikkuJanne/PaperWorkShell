"""Path-free review contracts for explicit removal after a verified export."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Never
from uuid import UUID

from paperworkshell.domain.case_lifecycle import MAX_SAFE_MANIFEST_INTEGER, require_case_id

_CONTEXT: Final = re.compile(r"[a-z][a-z0-9_.]{0,63}\Z")
# The existing package limit is 128 primary artifacts; the user may separately
# save one summary and one optional text companion from the same receipt.
MAX_PRESERVED_EXPORT_ARTIFACTS: Final = 130


class PostExportDeletionErrorCode(StrEnum):
    INVALID = "POST_EXPORT_DELETION_INVALID"
    VERIFIED_EXPORT_REQUIRED = "POST_EXPORT_DELETION_VERIFIED_EXPORT_REQUIRED"
    REVIEW_STALE = "POST_EXPORT_DELETION_REVIEW_STALE"
    CONFIRMATION_REQUIRED = "POST_EXPORT_DELETION_CONFIRMATION_REQUIRED"
    EXPORT_UNAVAILABLE = "POST_EXPORT_DELETION_EXPORT_UNAVAILABLE"
    CANCELLED = "POST_EXPORT_DELETION_CANCELLED"
    TIMED_OUT = "POST_EXPORT_DELETION_TIMED_OUT"
    STORAGE_FAILED = "POST_EXPORT_DELETION_STORAGE_FAILED"


_MESSAGES: Final = {
    PostExportDeletionErrorCode.INVALID: "The case-removal request is invalid. Review it again.",
    PostExportDeletionErrorCode.VERIFIED_EXPORT_REQUIRED: (
        "Export this case successfully before reviewing removal. No case files were removed."
    ),
    PostExportDeletionErrorCode.REVIEW_STALE: (
        "The case or removal review changed. Review removal again before confirming."
    ),
    PostExportDeletionErrorCode.CONFIRMATION_REQUIRED: (
        "Confirm removal after reviewing the case inventory and the exports that remain."
    ),
    PostExportDeletionErrorCode.EXPORT_UNAVAILABLE: (
        "A saved export is missing, changed, or unavailable. No case files were removed. "
        "Check the exported files before trying again."
    ),
    PostExportDeletionErrorCode.STORAGE_FAILED: (
        "PaperWorkShell could not complete case removal. Some case files may remain. "
        "The saved exports remain separate."
    ),
    PostExportDeletionErrorCode.CANCELLED: (
        "Case-removal preparation was cancelled. No case files were removed."
    ),
    PostExportDeletionErrorCode.TIMED_OUT: (
        "Case-removal preparation exceeded its time limit. No case files were removed."
    ),
}


@dataclass(slots=True, repr=False)
class PostExportDeletionError(ValueError):
    code: PostExportDeletionErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not PostExportDeletionErrorCode:
            self.code = PostExportDeletionErrorCode.INVALID
        if type(self.context) is not str or _CONTEXT.fullmatch(self.context) is None:
            self.context = "post_export.deletion"
        self.safe_message = _MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.safe_message)

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"

    def __repr__(self) -> str:
        return f"PostExportDeletionError(code={self.code.value!r}, context={self.context!r})"


def reject_post_export_deletion(code: PostExportDeletionErrorCode, context: str) -> Never:
    error = PostExportDeletionError(code, context)
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None


class PostExportDeletionConfirmation(StrEnum):
    REMOVE_AFTER_VERIFIED_EXPORT = "remove_after_verified_export"


@dataclass(frozen=True, slots=True)
class PostExportCaseInventory:
    """Counts only; all case-owned categories are included in removal."""

    source_count: int
    page_count: int
    overlay_count: int
    signature_image_count: int
    case_bytes: int
    cache_bytes: int
    temp_bytes: int
    entry_count: int

    def __post_init__(self) -> None:
        if (
            any(
                type(value) is not int or not 0 <= value <= MAX_SAFE_MANIFEST_INTEGER
                for value in (
                    self.source_count,
                    self.page_count,
                    self.overlay_count,
                    self.signature_image_count,
                    self.case_bytes,
                    self.cache_bytes,
                    self.temp_bytes,
                    self.entry_count,
                )
            )
            or max(self.cache_bytes, self.temp_bytes) > self.case_bytes
        ):
            reject_post_export_deletion(
                PostExportDeletionErrorCode.INVALID, "post_export.inventory"
            )


@dataclass(frozen=True, slots=True)
class PostExportDeletionInventory:
    """One review identity, separate from the later explicit destructive consent."""

    review_id: UUID = field(repr=False)
    case_id: UUID = field(repr=False)
    revision: int
    inventory: PostExportCaseInventory
    export_count: int
    export_names: tuple[str, ...] = field(repr=False)

    def __post_init__(self) -> None:
        require_case_id(self.review_id, context="post_export.review_id")
        require_case_id(self.case_id, context="post_export.case_id")
        if (
            type(self.revision) is not int
            or not 0 <= self.revision <= MAX_SAFE_MANIFEST_INTEGER
            or type(self.inventory) is not PostExportCaseInventory
            or type(self.export_count) is not int
            or not 1 <= self.export_count <= MAX_PRESERVED_EXPORT_ARTIFACTS
            or type(self.export_names) is not tuple
            or len(self.export_names) != self.export_count
            or any(
                type(value) is not str
                or not value
                or len(value) > 180
                or any(char in value for char in "\\/\0\r\n")
                for value in self.export_names
            )
        ):
            reject_post_export_deletion(PostExportDeletionErrorCode.INVALID, "post_export.review")

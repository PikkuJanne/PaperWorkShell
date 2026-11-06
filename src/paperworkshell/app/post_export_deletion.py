"""One-shot case-removal review, bound to current case and retained verified exports."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from threading import RLock
from typing import Protocol
from uuid import UUID, uuid4

from paperworkshell.domain.case_lifecycle import CaseDeletionResult
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.post_export_deletion import (
    PostExportCaseInventory,
    PostExportDeletionConfirmation,
    PostExportDeletionError,
    PostExportDeletionErrorCode,
    PostExportDeletionInventory,
    reject_post_export_deletion,
)


class PostExportDeletionStore(Protocol):
    def inspect(self, snapshot: ManifestSnapshot) -> PostExportCaseInventory: ...

    def delete(self, snapshot: ManifestSnapshot) -> CaseDeletionResult: ...


class ExportPreservationGuard(Protocol):
    def __call__(
        self,
        *,
        cancellation: Callable[[], bool] | None = None,
        deadline: Callable[[], bool] | None = None,
    ) -> AbstractContextManager[None]: ...


@dataclass(frozen=True, slots=True)
class _VerifiedExport:
    snapshot: ManifestSnapshot
    export_count: int
    export_names: tuple[str, ...]
    guard_exports: ExportPreservationGuard


class PostExportDeletionService:
    """Retain one trusted successful-export receipt, never durable destructive consent.

    The export adapter calls ``remember_verified_export`` only after complete,
    destination-reopened publication. Its guard reauthenticates all named outputs
    and holds them against mutation/deletion throughout the destructive step.
    A restart cannot resume removal; a new successful export and review are needed.
    """

    def __init__(
        self,
        store: PostExportDeletionStore,
        *,
        require_access: Callable[[UUID], None],
        close_case: Callable[[UUID], None],
    ) -> None:
        self._store = store
        self._require_access = require_access
        self._close_case = close_case
        self._export: _VerifiedExport | None = None
        self._review: PostExportDeletionInventory | None = None
        self._lock = RLock()

    def remember_verified_export(
        self,
        snapshot: ManifestSnapshot,
        *,
        export_count: int,
        export_names: tuple[str, ...],
        guard_exports: ExportPreservationGuard,
    ) -> None:
        """Trust only the application's verified export adapter, never a UI success flag."""

        with self._lock:
            self.clear()
            if type(snapshot) is not ManifestSnapshot or not callable(guard_exports):
                reject_post_export_deletion(
                    PostExportDeletionErrorCode.INVALID, "post_export.receipt"
                )
            # Validate output names/count without acquiring expensive storage authority.
            PostExportDeletionInventory(
                uuid4(),
                snapshot.case_id,
                snapshot.revision,
                PostExportCaseInventory(0, 0, 0, 0, 0, 0, 0, 0),
                export_count,
                export_names,
            )
            self._export = _VerifiedExport(snapshot, export_count, export_names, guard_exports)

    def prepare(
        self,
        snapshot: ManifestSnapshot,
        *,
        cancellation: Callable[[], bool] | None = None,
        deadline: Callable[[], bool] | None = None,
    ) -> PostExportDeletionInventory:
        with self._lock:
            self._review = None
            receipt = self._require_current(snapshot)
            self._require_access(snapshot.case_id)
            with receipt.guard_exports(cancellation=cancellation, deadline=deadline):
                inventory = self._store.inspect(snapshot)
            review = PostExportDeletionInventory(
                uuid4(),
                snapshot.case_id,
                snapshot.revision,
                inventory,
                receipt.export_count,
                receipt.export_names,
            )
            self._review = review
            return review

    def confirm(
        self,
        review_id: UUID,
        snapshot: ManifestSnapshot,
        confirmation: PostExportDeletionConfirmation,
        *,
        cancellation: Callable[[], bool] | None = None,
        deadline: Callable[[], bool] | None = None,
    ) -> CaseDeletionResult:
        with self._lock:
            if confirmation is not PostExportDeletionConfirmation.REMOVE_AFTER_VERIFIED_EXPORT:
                reject_post_export_deletion(
                    PostExportDeletionErrorCode.CONFIRMATION_REQUIRED, "post_export.confirm"
                )
            review = self._review
            self._review = None  # Every attempt consumes consent, including failed checks.
            receipt = self._require_current(snapshot)
            if review is None or type(review_id) is not UUID or review.review_id != review_id:
                reject_post_export_deletion(
                    PostExportDeletionErrorCode.REVIEW_STALE, "post_export.review"
                )
            self._require_access(snapshot.case_id)
            # Guard acquisition and fresh storage checks precede close or any deletion.
            with receipt.guard_exports(cancellation=cancellation, deadline=deadline):
                current = self._store.inspect(snapshot)
                if current != review.inventory:
                    reject_post_export_deletion(
                        PostExportDeletionErrorCode.REVIEW_STALE, "post_export.inventory_changed"
                    )
                self._require_access(snapshot.case_id)
                self._close_case(snapshot.case_id)
                # Never reuse the receipt after closure, partial removal, or a failed delete.
                self._export = None
                try:
                    result = self._store.delete(snapshot)
                except PostExportDeletionError:
                    raise
                except Exception:
                    reject_post_export_deletion(
                        PostExportDeletionErrorCode.STORAGE_FAILED, "post_export.delete"
                    )
                if type(result) is not CaseDeletionResult or result.case_id != snapshot.case_id:
                    reject_post_export_deletion(
                        PostExportDeletionErrorCode.STORAGE_FAILED, "post_export.result"
                    )
                return result

    def cancel(self) -> None:
        """Cancel only the review; it never closes or modifies the case."""
        with self._lock:
            self._review = None

    def clear(self) -> None:
        """Forget all authority on case change, lock, close, or a newer export attempt."""
        with self._lock:
            self._review = None
            self._export = None

    def _require_current(self, snapshot: ManifestSnapshot) -> _VerifiedExport:
        if type(snapshot) is not ManifestSnapshot:
            reject_post_export_deletion(PostExportDeletionErrorCode.INVALID, "post_export.snapshot")
        receipt = self._export
        if receipt is None:
            reject_post_export_deletion(
                PostExportDeletionErrorCode.VERIFIED_EXPORT_REQUIRED, "post_export.receipt_missing"
            )
        original = receipt.snapshot
        if (
            original.case_id != snapshot.case_id
            or original.revision != snapshot.revision
            or original.sha256 != snapshot.sha256
        ):
            reject_post_export_deletion(
                PostExportDeletionErrorCode.REVIEW_STALE, "post_export.snapshot_changed"
            )
        return receipt

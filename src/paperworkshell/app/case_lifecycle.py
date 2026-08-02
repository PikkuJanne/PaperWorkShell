"""Toolkit-free orchestration for exactly one active local case."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from typing import Protocol, runtime_checkable
from uuid import UUID

from paperworkshell.domain.case_lifecycle import (
    CaseDeletionConfirmation,
    CaseDeletionResult,
    CaseIndex,
    CaseLifecycleError,
    CaseLifecycleErrorCode,
    CaseSummary,
    reject_case_lifecycle,
    require_case_id,
)


@runtime_checkable
class OpenCaseLease(Protocol):
    """A storage-owned lease that keeps one authenticated case identity open."""

    @property
    def summary(self) -> CaseSummary:
        """Return immutable, path-free metadata for the leased case."""

    def close(self) -> None:
        """Release the lease idempotently."""


@runtime_checkable
class CaseLifecycleStore(Protocol):
    """Filesystem-independent lifecycle operations consumed by the application service."""

    def create_case(self) -> OpenCaseLease:
        """Atomically publish and lease a new valid case."""

    def list_cases(self) -> CaseIndex:
        """Return a deterministic local case index without changing storage."""

    def open_case(self, case_id: UUID) -> OpenCaseLease:
        """Validate and lease one current-version case without changing it."""

    def delete_case(
        self,
        case_id: UUID,
        confirmation: CaseDeletionConfirmation,
    ) -> CaseDeletionResult:
        """Remove one authenticated case after exact confirmation."""


@dataclass(frozen=True, slots=True)
class _ActiveCase:
    lease: OpenCaseLease
    summary: CaseSummary


class CaseLifecycleService:
    """Serialize lifecycle transitions while retaining at most one active lease."""

    __slots__ = ("_active", "_lock", "_store")

    def __init__(self, store: CaseLifecycleStore) -> None:
        self._store = store
        self._lock = RLock()
        self._active: _ActiveCase | None = None

    @property
    def active_case(self) -> CaseSummary | None:
        """Return the immutable active-case summary, if a lease is held."""

        with self._lock:
            return None if self._active is None else self._active.summary

    def create_case(self) -> CaseSummary:
        """Create and activate a case only after its storage commit succeeds."""

        with self._lock:
            self._require_no_active_case()
            return self._activate_from_store(
                self._store.create_case,
                failure_code=CaseLifecycleErrorCode.CREATE_FAILED,
                context="case.create",
            )

    def list_cases(self) -> CaseIndex:
        """List cases without changing the active case or local storage."""

        with self._lock:
            try:
                index = self._store.list_cases()
            except CaseLifecycleError:
                raise
            except Exception:
                reject_case_lifecycle(CaseLifecycleErrorCode.LIST_FAILED, "case.list")
            if not isinstance(index, CaseIndex):
                reject_case_lifecycle(CaseLifecycleErrorCode.LIST_FAILED, "case.list_result")
            return index

    def open_case(self, case_id: UUID) -> CaseSummary:
        """Open one case, treating an already-active matching case idempotently."""

        selected = require_case_id(case_id, context="case.open_id")
        with self._lock:
            if self._active is not None:
                if self._active.summary.case_id == selected:
                    return self._active.summary
                reject_case_lifecycle(
                    CaseLifecycleErrorCode.ACTIVE_CASE_CONFLICT,
                    "case.open_active",
                )
            return self._activate_from_store(
                lambda: self._store.open_case(selected),
                failure_code=CaseLifecycleErrorCode.OPEN_FAILED,
                context="case.open",
                expected_case_id=selected,
            )

    def close_case(self, case_id: UUID | None = None) -> CaseSummary | None:
        """Release the active lease, optionally requiring a matching internal ID."""

        selected = None if case_id is None else require_case_id(case_id, context="case.close_id")
        with self._lock:
            active = self._active
            if active is None:
                if selected is None:
                    return None
                reject_case_lifecycle(CaseLifecycleErrorCode.NO_ACTIVE_CASE, "case.close_active")
            if selected is not None and selected != active.summary.case_id:
                reject_case_lifecycle(
                    CaseLifecycleErrorCode.ACTIVE_CASE_MISMATCH,
                    "case.close_mismatch",
                )
            try:
                active.lease.close()
            except CaseLifecycleError:
                raise
            except Exception:
                reject_case_lifecycle(CaseLifecycleErrorCode.CLOSE_FAILED, "case.close")
            self._active = None
            return active.summary

    def delete_case(
        self,
        case_id: UUID,
        confirmation: CaseDeletionConfirmation,
    ) -> CaseDeletionResult:
        """Delete one inactive case after exact, non-coercible confirmation."""

        selected = require_case_id(case_id, context="case.delete_id")
        if confirmation is not CaseDeletionConfirmation.REMOVE_FROM_APPLICATION_STORAGE:
            reject_case_lifecycle(
                CaseLifecycleErrorCode.DELETE_CONFIRMATION_REQUIRED,
                "case.delete_confirmation",
            )
        with self._lock:
            if self._active is not None and self._active.summary.case_id == selected:
                reject_case_lifecycle(
                    CaseLifecycleErrorCode.DELETE_ACTIVE_CASE,
                    "case.delete_active",
                )
            try:
                result = self._store.delete_case(selected, confirmation)
            except CaseLifecycleError:
                raise
            except Exception:
                reject_case_lifecycle(CaseLifecycleErrorCode.DELETE_FAILED, "case.delete")
            if not isinstance(result, CaseDeletionResult) or result.case_id != selected:
                reject_case_lifecycle(
                    CaseLifecycleErrorCode.DELETE_FAILED,
                    "case.delete_result",
                )
            return result

    def _require_no_active_case(self) -> None:
        if self._active is not None:
            reject_case_lifecycle(
                CaseLifecycleErrorCode.ACTIVE_CASE_CONFLICT,
                "case.create_active",
            )

    def _activate_from_store(
        self,
        operation: Callable[[], OpenCaseLease],
        *,
        failure_code: CaseLifecycleErrorCode,
        context: str,
        expected_case_id: UUID | None = None,
    ) -> CaseSummary:
        try:
            lease = operation()
        except CaseLifecycleError:
            raise
        except Exception:
            reject_case_lifecycle(failure_code, context)
        try:
            summary = lease.summary
        except CaseLifecycleError:
            _close_rejected_lease(lease)
            raise
        except Exception:
            _close_rejected_lease(lease)
            reject_case_lifecycle(failure_code, context)
        if not isinstance(summary, CaseSummary) or (
            expected_case_id is not None and summary.case_id != expected_case_id
        ):
            _close_rejected_lease(lease)
            reject_case_lifecycle(failure_code, f"{context}_result")
        self._active = _ActiveCase(lease, summary)
        return summary


def _close_rejected_lease(lease: OpenCaseLease) -> None:
    try:
        lease.close()
    except Exception:
        return

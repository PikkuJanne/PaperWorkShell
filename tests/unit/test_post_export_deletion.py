"""Consent, current-state binding, failure, lock and restart removal regressions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest

from paperworkshell.app.post_export_deletion import PostExportDeletionService
from paperworkshell.domain.case_lifecycle import (
    CaseDeletionResult,
    CaseDeletionStatus,
    CaseLifecycleErrorCode,
)
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.post_export_deletion import (
    PostExportCaseInventory,
    PostExportDeletionConfirmation,
    PostExportDeletionError,
    PostExportDeletionErrorCode,
)
from paperworkshell.storage.case_lifecycle import build_initial_manifest_bytes


def _snapshot(case_id: UUID | None = None, *, revision: int = 0) -> ManifestSnapshot:
    selected = case_id or uuid4()
    payload = build_initial_manifest_bytes(selected, datetime(2026, 11, 2, tzinfo=UTC))
    document = json.loads(payload)
    document["revision"] = revision
    return ManifestSnapshot(
        selected,
        3,
        revision,
        document,
        hashlib.sha256(payload + str(revision).encode()).hexdigest(),
        "a" * 32,
    )


class Store:
    def __init__(self, case_id: UUID) -> None:
        self.inventory = PostExportCaseInventory(1, 2, 1, 1, 400, 100, 10, 16)
        self.result = CaseDeletionResult(case_id, CaseDeletionStatus.COMPLETE, 8, 7, 400)
        self.calls: list[str] = []
        self.fail_delete = False

    def inspect(self, snapshot: ManifestSnapshot) -> PostExportCaseInventory:
        self.calls.append("inspect")
        return self.inventory

    def delete(self, snapshot: ManifestSnapshot) -> CaseDeletionResult:
        self.calls.append("delete")
        if self.fail_delete:
            raise OSError("PRIVATE_TOKEN c:\\private\\document.pdf")
        return self.result


class Harness:
    def __init__(self) -> None:
        self.snapshot = _snapshot()
        self.store = Store(self.snapshot.case_id)
        self.events: list[str] = []
        self.locked = False
        self.export_missing = False
        self.guard_open = False
        self.service = PostExportDeletionService(
            self.store,
            require_access=self.access,
            close_case=self.close,
        )

    def access(self, case_id: UUID) -> None:
        assert case_id == self.snapshot.case_id
        self.events.append("access")
        if self.locked:
            raise PermissionError("locked")

    def close(self, case_id: UUID) -> None:
        assert self.guard_open
        self.events.append("close")

    @contextmanager
    def guard(
        self,
        *,
        cancellation: Callable[[], bool] | None = None,
        deadline: Callable[[], bool] | None = None,
    ) -> Iterator[None]:
        if cancellation is not None and cancellation():
            raise PostExportDeletionError(PostExportDeletionErrorCode.CANCELLED, "test.cancel")
        if deadline is not None and deadline():
            raise PostExportDeletionError(PostExportDeletionErrorCode.TIMED_OUT, "test.deadline")
        if self.export_missing:
            raise PostExportDeletionError(
                PostExportDeletionErrorCode.EXPORT_UNAVAILABLE, "test.guard"
            )
        self.guard_open = True
        self.events.append("guard_open")
        try:
            yield
        finally:
            self.guard_open = False
            self.events.append("guard_closed")

    def remember(self) -> None:
        self.service.remember_verified_export(
            self.snapshot,
            export_count=1,
            export_names=("packet.pdf",),
            guard_exports=self.guard,
        )

    def confirm(self, review_id: UUID) -> CaseDeletionResult:
        return self.service.confirm(
            review_id,
            self.snapshot,
            PostExportDeletionConfirmation.REMOVE_AFTER_VERIFIED_EXPORT,
        )


def test_success_requires_review_and_holds_exports_through_close_and_delete() -> None:
    h = Harness()
    h.remember()
    review = h.service.prepare(h.snapshot)
    assert review.inventory == h.store.inventory
    assert review.export_names == ("packet.pdf",)
    original_delete = h.store.delete

    def delete(snapshot: ManifestSnapshot) -> CaseDeletionResult:
        assert h.guard_open
        assert h.events[-1] == "close"
        return original_delete(snapshot)

    h.store.delete = delete  # type: ignore[method-assign]
    assert h.confirm(review.review_id).status is CaseDeletionStatus.COMPLETE
    assert h.store.calls == ["inspect", "inspect", "delete"]
    assert h.events[-1] == "guard_closed"
    with pytest.raises(PostExportDeletionError) as raised:
        h.confirm(review.review_id)
    assert raised.value.code is PostExportDeletionErrorCode.VERIFIED_EXPORT_REQUIRED


def test_cancel_never_closes_or_removes_and_requires_a_new_review() -> None:
    h = Harness()
    h.remember()
    review = h.service.prepare(h.snapshot)
    h.service.cancel()
    with pytest.raises(PostExportDeletionError) as raised:
        h.confirm(review.review_id)
    assert raised.value.code is PostExportDeletionErrorCode.REVIEW_STALE
    assert h.store.calls == ["inspect"]
    assert "close" not in h.events
    assert h.service.prepare(h.snapshot).review_id != review.review_id


@pytest.mark.parametrize("confirmation", [None, True, "remove_after_verified_export"])
def test_confirmation_is_exact_not_coercible(confirmation: object) -> None:
    h = Harness()
    h.remember()
    review = h.service.prepare(h.snapshot)
    with pytest.raises(PostExportDeletionError) as raised:
        h.service.confirm(
            review.review_id,
            h.snapshot,
            cast(PostExportDeletionConfirmation, confirmation),
        )
    assert raised.value.code is PostExportDeletionErrorCode.CONFIRMATION_REQUIRED
    assert "delete" not in h.store.calls


def test_locked_case_refuses_before_closure_and_consumes_pending_confirmation() -> None:
    h = Harness()
    h.remember()
    review = h.service.prepare(h.snapshot)
    h.locked = True
    with pytest.raises(PermissionError):
        h.confirm(review.review_id)
    assert "close" not in h.events
    assert "delete" not in h.store.calls
    h.locked = False
    with pytest.raises(PostExportDeletionError) as raised:
        h.confirm(review.review_id)
    assert raised.value.code is PostExportDeletionErrorCode.REVIEW_STALE


def test_restart_has_no_remembered_export_or_destructive_consent() -> None:
    h = Harness()
    h.remember()
    review = h.service.prepare(h.snapshot)
    h.service = PostExportDeletionService(h.store, require_access=h.access, close_case=h.close)
    with pytest.raises(PostExportDeletionError) as raised:
        h.confirm(review.review_id)
    assert raised.value.code is PostExportDeletionErrorCode.VERIFIED_EXPORT_REQUIRED
    assert "delete" not in h.store.calls


@pytest.mark.parametrize("change", ["case", "revision", "digest", "inventory"])
def test_changed_case_or_inventory_invalidates_review(change: str) -> None:
    h = Harness()
    h.remember()
    review = h.service.prepare(h.snapshot)
    if change == "case":
        h.snapshot = _snapshot()
    elif change == "revision":
        h.snapshot = _snapshot(h.snapshot.case_id, revision=1)
    elif change == "digest":
        h.snapshot = ManifestSnapshot(
            h.snapshot.case_id,
            3,
            0,
            h.snapshot.document,
            "0" * 64,
            "b" * 32,
        )
    else:
        h.store.inventory = replace(h.store.inventory, case_bytes=500)
    with pytest.raises(PostExportDeletionError) as raised:
        h.confirm(review.review_id)
    assert raised.value.code is PostExportDeletionErrorCode.REVIEW_STALE
    assert "close" not in h.events
    assert "delete" not in h.store.calls


def test_missing_changed_or_locked_export_preserves_open_case() -> None:
    h = Harness()
    h.remember()
    review = h.service.prepare(h.snapshot)
    h.export_missing = True
    with pytest.raises(PostExportDeletionError) as raised:
        h.confirm(review.review_id)
    assert raised.value.code is PostExportDeletionErrorCode.EXPORT_UNAVAILABLE
    assert "close" not in h.events
    assert "delete" not in h.store.calls


def test_partial_failure_reports_exact_limited_result_and_never_retries() -> None:
    h = Harness()
    h.store.result = CaseDeletionResult(
        h.snapshot.case_id,
        CaseDeletionStatus.PARTIAL,
        1,
        0,
        100,
        failure_code=CaseLifecycleErrorCode.DELETE_FAILED,
    )
    h.remember()
    review = h.service.prepare(h.snapshot)
    result = h.confirm(review.review_id)
    assert result.status is CaseDeletionStatus.PARTIAL
    assert "Some files may remain" in result.safe_message
    with pytest.raises(PostExportDeletionError):
        h.confirm(review.review_id)
    assert h.store.calls.count("delete") == 1


def test_storage_exception_is_sanitized_and_consumes_export_authority() -> None:
    h = Harness()
    h.store.fail_delete = True
    h.remember()
    review = h.service.prepare(h.snapshot)
    with pytest.raises(PostExportDeletionError) as raised:
        h.confirm(review.review_id)
    assert raised.value.code is PostExportDeletionErrorCode.STORAGE_FAILED
    assert "PRIVATE_TOKEN" not in str(raised.value)
    assert raised.value.__context__ is None
    with pytest.raises(PostExportDeletionError) as second:
        h.service.prepare(h.snapshot)
    assert second.value.code is PostExportDeletionErrorCode.VERIFIED_EXPORT_REQUIRED


def test_clear_on_lock_or_case_close_forgets_receipt() -> None:
    h = Harness()
    h.remember()
    h.service.prepare(h.snapshot)
    h.service.clear()
    with pytest.raises(PostExportDeletionError) as raised:
        h.service.prepare(h.snapshot)
    assert raised.value.code is PostExportDeletionErrorCode.VERIFIED_EXPORT_REQUIRED


@pytest.mark.parametrize("phase", ["prepare", "confirm"])
@pytest.mark.parametrize("probe", ["cancellation", "deadline"])
def test_operation_probes_refuse_before_closure_or_deletion(phase: str, probe: str) -> None:
    h = Harness()
    h.remember()
    review = h.service.prepare(h.snapshot)
    expected = (
        PostExportDeletionErrorCode.CANCELLED
        if probe == "cancellation"
        else PostExportDeletionErrorCode.TIMED_OUT
    )
    with pytest.raises(PostExportDeletionError) as raised:
        if phase == "prepare":
            if probe == "cancellation":
                h.service.prepare(h.snapshot, cancellation=lambda: True)
            else:
                h.service.prepare(h.snapshot, deadline=lambda: True)
        elif probe == "cancellation":
            h.service.confirm(
                review.review_id,
                h.snapshot,
                PostExportDeletionConfirmation.REMOVE_AFTER_VERIFIED_EXPORT,
                cancellation=lambda: True,
            )
        else:
            h.service.confirm(
                review.review_id,
                h.snapshot,
                PostExportDeletionConfirmation.REMOVE_AFTER_VERIFIED_EXPORT,
                deadline=lambda: True,
            )
    assert raised.value.code is expected
    assert "close" not in h.events
    assert "delete" not in h.store.calls

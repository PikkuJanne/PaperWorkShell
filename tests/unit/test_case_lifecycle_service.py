"""Unit coverage for serialized, toolkit-free case-lifecycle orchestration."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Barrier
from uuid import UUID

import pytest

from paperworkshell.app.case_lifecycle import (
    CaseLifecycleService,
    CaseLifecycleStore,
    OpenCaseLease,
)
from paperworkshell.domain.case_lifecycle import (
    CaseDeletionConfirmation,
    CaseDeletionResult,
    CaseDeletionStatus,
    CaseIndex,
    CaseLifecycleError,
    CaseLifecycleErrorCode,
    CaseSummary,
)

FIRST_ID = UUID("10000000-0000-4000-8000-000000000001")
SECOND_ID = UUID("20000000-0000-4000-8000-000000000002")
NOW = datetime(2026, 8, 2, 10, 0, tzinfo=UTC)


def _summary(case_id: UUID) -> CaseSummary:
    return CaseSummary(case_id, 1, 0, NOW, NOW)


@dataclass(slots=True)
class FakeLease:
    summary: CaseSummary
    close_error: Exception | None = None
    close_calls: int = 0

    def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class FakeStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, UUID | None]] = []
        self.create_lease = FakeLease(_summary(FIRST_ID))
        self.open_leases: dict[UUID, FakeLease] = {
            FIRST_ID: FakeLease(_summary(FIRST_ID)),
            SECOND_ID: FakeLease(_summary(SECOND_ID)),
        }
        self.index = CaseIndex(cases=(_summary(FIRST_ID), _summary(SECOND_ID)))
        self.delete_results: dict[UUID, CaseDeletionResult] = {
            FIRST_ID: _complete_deletion(FIRST_ID),
            SECOND_ID: _complete_deletion(SECOND_ID),
        }
        self.failures: dict[str, Exception] = {}

    def create_case(self) -> FakeLease:
        self.calls.append(("create", None))
        self._raise_failure("create")
        return self.create_lease

    def list_cases(self) -> CaseIndex:
        self.calls.append(("list", None))
        self._raise_failure("list")
        return self.index

    def open_case(self, case_id: UUID) -> FakeLease:
        self.calls.append(("open", case_id))
        self._raise_failure("open")
        return self.open_leases[case_id]

    def delete_case(
        self,
        case_id: UUID,
        confirmation: CaseDeletionConfirmation,
    ) -> CaseDeletionResult:
        assert confirmation is CaseDeletionConfirmation.REMOVE_FROM_APPLICATION_STORAGE
        self.calls.append(("delete", case_id))
        self._raise_failure("delete")
        return self.delete_results[case_id]

    def _raise_failure(self, operation: str) -> None:
        failure = self.failures.get(operation)
        if failure is not None:
            raise failure


def _complete_deletion(case_id: UUID) -> CaseDeletionResult:
    return CaseDeletionResult(case_id, CaseDeletionStatus.COMPLETE, 1, 7, 100)


def _error_code(callable_: object) -> CaseLifecycleErrorCode:
    assert callable(callable_)
    try:
        callable_()
    except CaseLifecycleError as error:
        return error.code
    raise AssertionError("expected a lifecycle error")


def test_fake_contracts_are_structurally_runtime_checkable() -> None:
    store = FakeStore()

    assert isinstance(store, CaseLifecycleStore)
    assert isinstance(store.create_lease, OpenCaseLease)


def test_create_activates_only_after_store_success() -> None:
    store = FakeStore()
    service = CaseLifecycleService(store)

    created = service.create_case()

    assert created == _summary(FIRST_ID)
    assert service.active_case == created
    assert store.calls == [("create", None)]


def test_create_conflict_is_rejected_before_second_store_call() -> None:
    store = FakeStore()
    service = CaseLifecycleService(store)
    service.create_case()

    with pytest.raises(CaseLifecycleError) as raised:
        service.create_case()

    assert raised.value.code is CaseLifecycleErrorCode.ACTIVE_CASE_CONFLICT
    assert store.calls == [("create", None)]
    assert service.active_case == _summary(FIRST_ID)


@pytest.mark.parametrize(
    ("operation", "expected_code"),
    [
        ("create", CaseLifecycleErrorCode.CREATE_FAILED),
        ("open", CaseLifecycleErrorCode.OPEN_FAILED),
    ],
)
def test_failed_activation_never_sets_active_state(
    operation: str,
    expected_code: CaseLifecycleErrorCode,
) -> None:
    store = FakeStore()
    store.failures[operation] = OSError("raw path and operating-system detail")
    service = CaseLifecycleService(store)

    with pytest.raises(CaseLifecycleError) as raised:
        if operation == "create":
            service.create_case()
        else:
            service.open_case(FIRST_ID)

    assert raised.value.code is expected_code
    assert "raw path" not in str(raised.value)
    assert service.active_case is None


def test_store_lifecycle_error_is_preserved_without_state_change() -> None:
    store = FakeStore()
    expected = CaseLifecycleError(
        CaseLifecycleErrorCode.MANIFEST_VERSION_UNSUPPORTED,
        "case.version",
    )
    store.failures["open"] = expected
    service = CaseLifecycleService(store)

    with pytest.raises(CaseLifecycleError) as raised:
        service.open_case(FIRST_ID)

    assert raised.value is expected
    assert service.active_case is None


def test_same_id_open_is_idempotent_and_different_id_conflicts_before_store() -> None:
    store = FakeStore()
    service = CaseLifecycleService(store)
    first = service.open_case(FIRST_ID)

    repeated = service.open_case(FIRST_ID)
    with pytest.raises(CaseLifecycleError) as conflict:
        service.open_case(SECOND_ID)

    assert repeated is first
    assert conflict.value.code is CaseLifecycleErrorCode.ACTIVE_CASE_CONFLICT
    assert store.calls == [("open", FIRST_ID)]
    assert service.active_case == first


def test_open_rejects_mismatched_store_result_and_closes_lease() -> None:
    store = FakeStore()
    mismatched = FakeLease(_summary(SECOND_ID))
    store.open_leases[FIRST_ID] = mismatched
    service = CaseLifecycleService(store)

    with pytest.raises(CaseLifecycleError) as raised:
        service.open_case(FIRST_ID)

    assert raised.value.code is CaseLifecycleErrorCode.OPEN_FAILED
    assert mismatched.close_calls == 1
    assert service.active_case is None


def test_list_returns_typed_index_without_changing_active_case() -> None:
    store = FakeStore()
    service = CaseLifecycleService(store)
    active = service.open_case(FIRST_ID)

    index = service.list_cases()

    assert index is store.index
    assert service.active_case is active
    assert store.calls == [("open", FIRST_ID), ("list", None)]


def test_raw_list_failure_is_sanitized_and_preserves_active_case() -> None:
    store = FakeStore()
    service = CaseLifecycleService(store)
    active = service.open_case(FIRST_ID)
    store.failures["list"] = OSError(r"C:\private\case")

    with pytest.raises(CaseLifecycleError) as raised:
        service.list_cases()

    assert raised.value.code is CaseLifecycleErrorCode.LIST_FAILED
    assert "private" not in str(raised.value)
    assert service.active_case is active


def test_close_is_idempotent_without_id_and_rejects_mismatch_before_lease_call() -> None:
    store = FakeStore()
    service = CaseLifecycleService(store)

    assert service.close_case() is None
    with pytest.raises(CaseLifecycleError) as no_active:
        service.close_case(FIRST_ID)
    active = service.open_case(FIRST_ID)
    lease = store.open_leases[FIRST_ID]
    with pytest.raises(CaseLifecycleError) as mismatch:
        service.close_case(SECOND_ID)

    assert no_active.value.code is CaseLifecycleErrorCode.NO_ACTIVE_CASE
    assert mismatch.value.code is CaseLifecycleErrorCode.ACTIVE_CASE_MISMATCH
    assert lease.close_calls == 0
    assert service.active_case is active


def test_successful_close_releases_matching_lease_and_clears_active_case() -> None:
    store = FakeStore()
    service = CaseLifecycleService(store)
    active = service.open_case(FIRST_ID)
    lease = store.open_leases[FIRST_ID]

    closed = service.close_case(FIRST_ID)

    assert closed is active
    assert lease.close_calls == 1
    assert service.active_case is None


def test_close_failure_keeps_active_case_for_an_explicit_retry() -> None:
    store = FakeStore()
    service = CaseLifecycleService(store)
    active = service.open_case(FIRST_ID)
    lease = store.open_leases[FIRST_ID]
    lease.close_error = OSError("raw handle detail")

    with pytest.raises(CaseLifecycleError) as raised:
        service.close_case(FIRST_ID)

    assert raised.value.code is CaseLifecycleErrorCode.CLOSE_FAILED
    assert "raw handle" not in str(raised.value)
    assert service.active_case is active


def test_delete_requires_exact_enum_before_store_call() -> None:
    store = FakeStore()
    service = CaseLifecycleService(store)

    with pytest.raises(CaseLifecycleError) as raised:
        service.delete_case(
            FIRST_ID,
            "remove_from_application_storage",  # type: ignore[arg-type]
        )

    assert raised.value.code is CaseLifecycleErrorCode.DELETE_CONFIRMATION_REQUIRED
    assert store.calls == []


def test_delete_active_case_is_rejected_but_inactive_sibling_is_allowed() -> None:
    store = FakeStore()
    service = CaseLifecycleService(store)
    active = service.open_case(FIRST_ID)

    with pytest.raises(CaseLifecycleError) as raised:
        service.delete_case(
            FIRST_ID,
            CaseDeletionConfirmation.REMOVE_FROM_APPLICATION_STORAGE,
        )
    result = service.delete_case(
        SECOND_ID,
        CaseDeletionConfirmation.REMOVE_FROM_APPLICATION_STORAGE,
    )

    assert raised.value.code is CaseLifecycleErrorCode.DELETE_ACTIVE_CASE
    assert result == _complete_deletion(SECOND_ID)
    assert store.calls == [("open", FIRST_ID), ("delete", SECOND_ID)]
    assert service.active_case is active


def test_raw_delete_failure_is_sanitized_and_preserves_active_sibling() -> None:
    store = FakeStore()
    service = CaseLifecycleService(store)
    active = service.open_case(FIRST_ID)
    store.failures["delete"] = OSError(r"C:\private\applicant")

    with pytest.raises(CaseLifecycleError) as raised:
        service.delete_case(
            SECOND_ID,
            CaseDeletionConfirmation.REMOVE_FROM_APPLICATION_STORAGE,
        )

    assert raised.value.code is CaseLifecycleErrorCode.DELETE_FAILED
    assert "applicant" not in str(raised.value)
    assert service.active_case is active


def test_simultaneous_opens_have_one_active_winner_and_one_store_call() -> None:
    store = FakeStore()
    service = CaseLifecycleService(store)
    start = Barrier(3)

    def attempt(case_id: UUID) -> tuple[str, UUID | CaseLifecycleErrorCode]:
        start.wait()
        try:
            return ("opened", service.open_case(case_id).case_id)
        except CaseLifecycleError as error:
            return ("failed", error.code)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(attempt, FIRST_ID)
        second = executor.submit(attempt, SECOND_ID)
        start.wait()
        outcomes = (first.result(), second.result())

    assert sum(outcome[0] == "opened" for outcome in outcomes) == 1
    assert (
        sum(
            outcome == ("failed", CaseLifecycleErrorCode.ACTIVE_CASE_CONFLICT)
            for outcome in outcomes
        )
        == 1
    )
    assert [call for call in store.calls if call[0] == "open"] in (
        [("open", FIRST_ID)],
        [("open", SECOND_ID)],
    )
    assert service.active_case is not None


def test_helper_reports_expected_error_code() -> None:
    service = CaseLifecycleService(FakeStore())

    code = _error_code(lambda: service.close_case(FIRST_ID))

    assert code is CaseLifecycleErrorCode.NO_ACTIVE_CASE

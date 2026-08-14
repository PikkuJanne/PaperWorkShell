from __future__ import annotations

import pytest

from paperworkshell.domain.incremental_processing import (
    INCREMENTAL_PROCESSING_POLICY_VERSION,
    IncrementalPageScheduler,
    IncrementalProcessingError,
    IncrementalProcessingErrorCode,
    IncrementalProcessingLimits,
    IncrementalProcessingSnapshot,
)
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1


def _limits(*, temporary_bytes: int = 100) -> IncrementalProcessingLimits:
    return IncrementalProcessingLimits(
        maximum_pages=10,
        maximum_full_resolution_page_buffers=(
            CONSTRAINED_RESOURCE_POLICY_V1.maximum_active_full_resolution_page_buffers
        ),
        maximum_page_results=1,
        maximum_temporary_storage_bytes=temporary_bytes,
    )


def test_scheduler_processes_pages_in_order_and_records_only_numeric_high_water() -> None:
    observations: list[IncrementalProcessingSnapshot] = []
    scheduler = IncrementalPageScheduler(3, _limits(), observer=observations.append)

    with scheduler.begin_page(0) as page:
        page.observe(
            full_resolution_page_buffers=1,
            page_results=0,
            temporary_storage_bytes=40,
        )
        page.observe(
            full_resolution_page_buffers=1,
            page_results=1,
            temporary_storage_bytes=75,
        )
        page.observe(
            full_resolution_page_buffers=0,
            page_results=0,
            temporary_storage_bytes=0,
        )
    for page_index in (1, 2):
        with scheduler.begin_page(page_index) as page:
            page.observe(
                full_resolution_page_buffers=0,
                page_results=1,
                temporary_storage_bytes=25,
            )
            page.observe(
                full_resolution_page_buffers=0,
                page_results=0,
                temporary_storage_bytes=0,
            )

    final = scheduler.require_complete()
    assert final.completed_pages == final.total_pages == 3
    assert final.active_pages == 0
    assert final.peak_full_resolution_page_buffers == 1
    assert final.peak_page_results == 1
    assert final.peak_temporary_storage_bytes == 75
    assert all(not hasattr(item, "page_id") for item in observations)
    assert "75" not in repr(scheduler)


@pytest.mark.parametrize(
    ("buffers", "results", "temporary_bytes"),
    ((2, 0, 1), (0, 2, 1), (0, 0, 101)),
)
def test_scheduler_rejects_page_resource_limit_and_allows_clean_retry(
    buffers: int,
    results: int,
    temporary_bytes: int,
) -> None:
    scheduler = IncrementalPageScheduler(1, _limits())
    with pytest.raises(IncrementalProcessingError) as raised:
        with scheduler.begin_page(0) as page:
            page.observe(
                full_resolution_page_buffers=buffers,
                page_results=results,
                temporary_storage_bytes=temporary_bytes,
            )
    assert raised.value.code is IncrementalProcessingErrorCode.RESOURCE_LIMIT
    assert raised.value.page_index == 0
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None

    with scheduler.begin_page(0) as retry:
        retry.observe(
            full_resolution_page_buffers=1,
            page_results=1,
            temporary_storage_bytes=100,
        )
        retry.observe(
            full_resolution_page_buffers=0,
            page_results=0,
            temporary_storage_bytes=0,
        )
    assert scheduler.require_complete().completed_pages == 1


def test_scheduler_rejects_overlap_skip_and_unreleased_completion() -> None:
    scheduler = IncrementalPageScheduler(2, _limits())
    first = scheduler.begin_page(0)
    with pytest.raises(IncrementalProcessingError) as overlap:
        scheduler.begin_page(0)
    assert overlap.value.code is IncrementalProcessingErrorCode.PAGE_STATE_INVALID
    first.observe(
        full_resolution_page_buffers=1,
        page_results=0,
        temporary_storage_bytes=1,
    )
    with pytest.raises(IncrementalProcessingError) as retained:
        first.complete()
    assert retained.value.code is IncrementalProcessingErrorCode.PAGE_STATE_INVALID
    first.abort()

    with pytest.raises(IncrementalProcessingError) as skipped:
        scheduler.begin_page(1)
    assert skipped.value.code is IncrementalProcessingErrorCode.PAGE_ORDER_INVALID


def test_scheduler_observer_failure_is_sanitized_and_recoverable() -> None:
    calls = 0

    def observer(_snapshot: IncrementalProcessingSnapshot) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("PRIVATE-CONTENT-CANARY")

    scheduler = IncrementalPageScheduler(1, _limits(), observer=observer)
    with pytest.raises(IncrementalProcessingError) as raised:
        scheduler.begin_page(0)
    assert raised.value.code is IncrementalProcessingErrorCode.OBSERVER_FAILED
    assert "PRIVATE" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert scheduler.snapshot().active_page_index is None

    with scheduler.begin_page(0) as retry:
        retry.observe(
            full_resolution_page_buffers=0,
            page_results=0,
            temporary_storage_bytes=0,
        )
    assert scheduler.require_complete().completed_pages == 1


def test_incremental_policy_is_versioned_and_bound_to_central_one_page_limit() -> None:
    limits = _limits()
    assert limits.policy_version == INCREMENTAL_PROCESSING_POLICY_VERSION == 1
    assert limits.maximum_full_resolution_page_buffers == 1
    with pytest.raises(IncrementalProcessingError):
        IncrementalProcessingLimits(10, 2, 1, 100)


@pytest.mark.parametrize("failure_call", (3, 5, 6))
def test_scheduler_rolls_back_observe_complete_and_abort_when_observer_fails(
    failure_call: int,
) -> None:
    calls = 0

    def observer(_snapshot: IncrementalProcessingSnapshot) -> None:
        nonlocal calls
        calls += 1
        if calls == failure_call:
            raise RuntimeError("PRIVATE-CONTENT-CANARY")

    scheduler = IncrementalPageScheduler(1, _limits(), observer=observer)
    lease = scheduler.begin_page(0)
    if failure_call == 3:
        with pytest.raises(IncrementalProcessingError):
            lease.observe(
                full_resolution_page_buffers=1,
                page_results=0,
                temporary_storage_bytes=10,
            )
        snapshot = scheduler.snapshot()
        assert snapshot.active_full_resolution_page_buffers == 0
        assert snapshot.peak_full_resolution_page_buffers == 0
    else:
        lease.observe(
            full_resolution_page_buffers=1,
            page_results=0,
            temporary_storage_bytes=10,
        )
        lease.observe(
            full_resolution_page_buffers=0,
            page_results=0,
            temporary_storage_bytes=0,
        )
        if failure_call == 5:
            with pytest.raises(IncrementalProcessingError):
                lease.complete()
            assert scheduler.snapshot().active_page_index == 0
            assert scheduler.snapshot().completed_pages == 0
        else:
            lease.observe(
                full_resolution_page_buffers=0,
                page_results=0,
                temporary_storage_bytes=0,
            )
            with pytest.raises(IncrementalProcessingError):
                lease.abort()
            assert scheduler.snapshot().active_page_index == 0

    lease.abort()
    with scheduler.begin_page(0) as retry:
        retry.observe(
            full_resolution_page_buffers=0,
            page_results=0,
            temporary_storage_bytes=0,
        )
    assert scheduler.require_complete().completed_pages == 1

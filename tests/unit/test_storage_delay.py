from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.helpers.storage_delay import DeterministicStorageDelay, StorageDelayOperation


@pytest.mark.parametrize("operation", tuple(StorageDelayOperation))
def test_storage_delay_pauses_each_closed_operation_until_explicit_release(
    operation: StorageDelayOperation,
) -> None:
    delay = DeterministicStorageDelay(frozenset({operation}))
    wrapped = delay.wrap(operation, lambda value: value + 1)

    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(wrapped, 41)
        assert delay.entered.wait(timeout=1)
        assert not result.done()
        delay.release.set()
        assert result.result(timeout=1) == 42

    assert delay.observed == (operation,)


def test_storage_delay_rejects_open_ended_operation_names() -> None:
    with pytest.raises(ValueError):
        DeterministicStorageDelay(frozenset({"write"}))  # type: ignore[arg-type]


def test_storage_delay_cancellation_preempts_an_unreleased_boundary() -> None:
    delay = DeterministicStorageDelay(frozenset({StorageDelayOperation.WRITE}))
    cancelled = False

    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(
            delay.checkpoint,
            StorageDelayOperation.WRITE,
            cancellation=lambda: cancelled,
        )
        assert delay.entered.wait(timeout=1)
        cancelled = True
        assert result.result(timeout=1) is False

    assert not delay.release.is_set()
    assert delay.observed == (StorageDelayOperation.WRITE,)

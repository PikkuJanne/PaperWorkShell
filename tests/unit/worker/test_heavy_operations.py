from __future__ import annotations

import threading

from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator


def test_shared_gate_applies_backpressure_and_reports_observed_concurrency() -> None:
    coordinator = HeavyOperationCoordinator(1)
    first = coordinator.try_acquire(0.01)
    assert first is not None
    acquired_second = threading.Event()
    release_second = threading.Event()

    def wait_for_slot() -> None:
        lease = None
        while lease is None:
            lease = coordinator.try_acquire(0.01)
        with lease:
            acquired_second.set()
            release_second.wait(1)

    thread = threading.Thread(target=wait_for_slot)
    with first:
        thread.start()
        assert not acquired_second.wait(0.05)
        snapshot = coordinator.snapshot()
        assert snapshot.active_operations == 1
        assert snapshot.waiting_operations in {0, 1}
    assert acquired_second.wait(1)
    release_second.set()
    thread.join(1)
    assert not thread.is_alive()
    assert coordinator.snapshot().maximum_observed_concurrency == 1


def test_two_slot_gate_reports_real_overlap() -> None:
    coordinator = HeavyOperationCoordinator(2)
    first = coordinator.try_acquire(0.01)
    second = coordinator.try_acquire(0.01)
    assert first is not None and second is not None
    with first, second:
        assert coordinator.snapshot().active_operations == 2
        assert coordinator.snapshot().maximum_observed_concurrency == 2
    assert coordinator.snapshot().active_operations == 0

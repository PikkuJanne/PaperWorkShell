"""Process-local backpressure shared by document-worker compositions."""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HeavyOperationSnapshot:
    """Content-free concurrency accounting safe for resource evidence."""

    maximum_concurrency: int
    active_operations: int
    waiting_operations: int
    maximum_observed_concurrency: int


class HeavyOperationLease:
    """One exact coordinator slot, released once at context exit."""

    __slots__ = ("_coordinator", "_released")

    def __init__(self, coordinator: HeavyOperationCoordinator) -> None:
        self._coordinator = coordinator
        self._released = False

    def __enter__(self) -> HeavyOperationLease:
        if self._released:
            raise RuntimeError("heavy-operation lease is already released")
        return self

    def __exit__(self, *_exc: object) -> None:
        if not self._released:
            self._released = True
            self._coordinator._release()


class HeavyOperationCoordinator:
    """Bound active heavy work and apply FIFO-neutral blocking backpressure."""

    __slots__ = (
        "_active",
        "_condition",
        "_maximum",
        "_maximum_observed",
        "_waiting",
    )

    def __init__(self, maximum_concurrency: int) -> None:
        if type(maximum_concurrency) is not int or maximum_concurrency <= 0:
            raise ValueError("maximum_concurrency must be a positive integer")
        self._maximum = maximum_concurrency
        self._condition = threading.Condition()
        self._active = 0
        self._waiting = 0
        self._maximum_observed = 0

    def try_acquire(self, timeout_seconds: float) -> HeavyOperationLease | None:
        """Acquire within one bounded polling interval, or leave state unchanged."""

        if type(timeout_seconds) not in {int, float}:
            raise ValueError("timeout_seconds must be a finite positive number")
        timeout = float(timeout_seconds)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout_seconds must be a finite positive number")
        with self._condition:
            self._waiting += 1
            try:
                acquired = self._condition.wait_for(
                    lambda: self._active < self._maximum,
                    timeout=timeout,
                )
                if not acquired:
                    return None
                self._active += 1
                self._maximum_observed = max(self._maximum_observed, self._active)
                return HeavyOperationLease(self)
            finally:
                self._waiting -= 1

    def snapshot(self) -> HeavyOperationSnapshot:
        """Return one atomic, content-free observation."""

        with self._condition:
            return HeavyOperationSnapshot(
                maximum_concurrency=self._maximum,
                active_operations=self._active,
                waiting_operations=self._waiting,
                maximum_observed_concurrency=self._maximum_observed,
            )

    def _release(self) -> None:
        with self._condition:
            if self._active <= 0:
                raise RuntimeError("heavy-operation coordinator release is unbalanced")
            self._active -= 1
            self._condition.notify()

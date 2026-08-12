"""Deterministic test-only storage-delay adapter for bounded orchestration probes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from threading import Event, Lock


class StorageDelayOperation(StrEnum):
    """Closed storage boundaries that a test may delay without hardware claims."""

    READ = "read"
    WRITE = "write"
    FLUSH = "flush"
    RENAME = "rename"
    DIRECTORY = "directory"


@dataclass(slots=True)
class DeterministicStorageDelay:
    """Pause selected operations until the test explicitly releases them."""

    delayed_operations: frozenset[StorageDelayOperation]
    entered: Event = field(default_factory=Event, repr=False)
    release: Event = field(default_factory=Event, repr=False)
    _observed: list[StorageDelayOperation] = field(default_factory=list, repr=False)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def __post_init__(self) -> None:
        if type(self.delayed_operations) is not frozenset or any(
            type(item) is not StorageDelayOperation for item in self.delayed_operations
        ):
            raise ValueError("delayed_operations must contain closed storage operations")

    @property
    def observed(self) -> tuple[StorageDelayOperation, ...]:
        with self._lock:
            return tuple(self._observed)

    def checkpoint(
        self,
        operation: StorageDelayOperation,
        *,
        cancellation: Callable[[], bool] | None = None,
    ) -> bool:
        """Pause at one boundary and return false when cancellation wins the race."""

        if type(operation) is not StorageDelayOperation:
            raise ValueError("operation must be a StorageDelayOperation")
        if cancellation is not None and not callable(cancellation):
            raise ValueError("cancellation must be callable")
        with self._lock:
            self._observed.append(operation)
        if operation in self.delayed_operations:
            self.entered.set()
            for _attempt in range(1_000):
                if self.release.wait(timeout=0.01):
                    return True
                if cancellation is not None and cancellation():
                    return False
            raise TimeoutError("deterministic storage delay was not released")
        return True

    def wrap(
        self, operation: StorageDelayOperation, callback: Callable[..., object]
    ) -> Callable[..., object]:
        """Return a transparent callable that pauses at one selected boundary."""

        if not callable(callback):
            raise ValueError("callback must be callable")

        def delayed(*args: object, **kwargs: object) -> object:
            self.checkpoint(operation)
            return callback(*args, **kwargs)

        return delayed

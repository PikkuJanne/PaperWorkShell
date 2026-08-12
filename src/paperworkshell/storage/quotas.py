"""Bounded case-workspace quota policy and in-process reservations."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from enum import StrEnum
from threading import Lock
from typing import Final, Self
from uuid import UUID, uuid4

from paperworkshell.storage.errors import WorkspaceErrorCode, reject

MAX_SAFE_QUOTA_INTEGER: Final = 9_007_199_254_740_991
MINIMUM_WORKSPACE_ENTRY_COUNT: Final = 6
MINIMUM_WORKSPACE_DEPTH: Final = 2
MAXIMUM_WORKSPACE_DEPTH: Final = 64


class QuotaArea(StrEnum):
    """Closed writable areas covered by a reservation."""

    CACHE = "cache"
    TEMP = "temp"


@dataclass(frozen=True, slots=True)
class WorkspaceQuota:
    """Caller-supplied limits; PWS-0101 intentionally guesses no product defaults."""

    maximum_case_bytes: int
    maximum_cache_bytes: int
    maximum_temp_bytes: int
    maximum_entries: int
    maximum_depth: int

    def __post_init__(self) -> None:
        values = (
            self.maximum_case_bytes,
            self.maximum_cache_bytes,
            self.maximum_temp_bytes,
            self.maximum_entries,
            self.maximum_depth,
        )
        if any(
            type(value) is not int or not 1 <= value <= MAX_SAFE_QUOTA_INTEGER for value in values
        ):
            reject(
                WorkspaceErrorCode.QUOTA_INVALID,
                "quota.configuration",
                "Workspace quotas must be positive bounded integers.",
            )
        if self.maximum_cache_bytes > self.maximum_case_bytes:
            reject(
                WorkspaceErrorCode.QUOTA_INVALID,
                "quota.cache",
                "The cache quota cannot exceed the complete case quota.",
            )
        if self.maximum_temp_bytes > self.maximum_case_bytes:
            reject(
                WorkspaceErrorCode.QUOTA_INVALID,
                "quota.temp",
                "The temporary-storage quota cannot exceed the complete case quota.",
            )
        if self.maximum_entries < MINIMUM_WORKSPACE_ENTRY_COUNT:
            reject(
                WorkspaceErrorCode.QUOTA_INVALID,
                "quota.entries",
                "The entry quota must include the fixed empty workspace layout.",
            )
        if not MINIMUM_WORKSPACE_DEPTH <= self.maximum_depth <= MAXIMUM_WORKSPACE_DEPTH:
            reject(
                WorkspaceErrorCode.QUOTA_INVALID,
                "quota.depth",
                "The inventory depth must be within the supported workspace range.",
            )


@dataclass(frozen=True, slots=True)
class WorkspaceUsage:
    """Logical file usage from one bounded, no-follow workspace inventory."""

    case_bytes: int
    cache_bytes: int
    temp_bytes: int
    entry_count: int
    maximum_depth_seen: int


@dataclass(frozen=True, slots=True)
class _ReservedCapacity:
    area: QuotaArea
    byte_count: int
    entry_count: int


@dataclass(frozen=True, slots=True)
class QuotaReservationSnapshot:
    """Content-free point-in-time totals for active capacity claims."""

    case_bytes: int
    cache_bytes: int
    temp_bytes: int
    entry_count: int

    @property
    def is_empty(self) -> bool:
        """Return whether no capacity remains claimed."""

        return not (self.case_bytes or self.cache_bytes or self.temp_bytes or self.entry_count)


@dataclass(frozen=True, slots=True)
class QuotaReservationObservationSnapshot:
    """Current and event-driven high-water claims for one observation window."""

    current: QuotaReservationSnapshot
    high_water: QuotaReservationSnapshot


@dataclass(slots=True)
class _QuotaReservationObservationState:
    peak_case_bytes: int
    peak_cache_bytes: int
    peak_temp_bytes: int
    peak_entry_count: int

    @classmethod
    def from_snapshot(
        cls,
        snapshot: QuotaReservationSnapshot,
    ) -> _QuotaReservationObservationState:
        return cls(
            snapshot.case_bytes,
            snapshot.cache_bytes,
            snapshot.temp_bytes,
            snapshot.entry_count,
        )

    def record(self, snapshot: QuotaReservationSnapshot) -> None:
        self.peak_case_bytes = max(self.peak_case_bytes, snapshot.case_bytes)
        self.peak_cache_bytes = max(self.peak_cache_bytes, snapshot.cache_bytes)
        self.peak_temp_bytes = max(self.peak_temp_bytes, snapshot.temp_bytes)
        self.peak_entry_count = max(self.peak_entry_count, snapshot.entry_count)

    def measurement(
        self,
        current: QuotaReservationSnapshot,
    ) -> QuotaReservationObservationSnapshot:
        return QuotaReservationObservationSnapshot(
            current=current,
            high_water=QuotaReservationSnapshot(
                case_bytes=self.peak_case_bytes,
                cache_bytes=self.peak_cache_bytes,
                temp_bytes=self.peak_temp_bytes,
                entry_count=self.peak_entry_count,
            ),
        )


class QuotaReservationObservation(AbstractContextManager["QuotaReservationObservation"]):
    """Retain an event-driven high-water observation over one reservation book."""

    __slots__ = ("_book", "_closed", "_final", "_lock", "_token")

    def __init__(self, book: QuotaReservationBook, token: UUID) -> None:
        self._book = book
        self._token = token
        self._lock = Lock()
        self._closed = False
        self._final: QuotaReservationObservationSnapshot | None = None

    @property
    def snapshot(self) -> QuotaReservationObservationSnapshot:
        """Return one synchronized current/high-water measurement."""

        with self._lock:
            if self._final is not None:
                return self._final
            return self._book._observation_snapshot(self._token)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> QuotaReservationObservationSnapshot:
        """Stop observing and return the final measurement idempotently."""

        with self._lock:
            if not self._closed:
                self._final = self._book._end_observation(self._token)
                self._closed = True
            assert self._final is not None
            return self._final


class QuotaReservation(AbstractContextManager["QuotaReservation"]):
    """A conservative in-process capacity claim released explicitly or on exit."""

    __slots__ = ("_book", "_capacity", "_closed", "_token")

    def __init__(
        self,
        book: QuotaReservationBook,
        token: UUID,
        capacity: _ReservedCapacity,
    ) -> None:
        self._book = book
        self._capacity = capacity
        self._closed = False
        self._token = token

    @property
    def token(self) -> UUID:
        return self._token

    @property
    def area(self) -> QuotaArea:
        return self._capacity.area

    @property
    def byte_count(self) -> int:
        return self._capacity.byte_count

    @property
    def entry_count(self) -> int:
        return self._capacity.entry_count

    @property
    def is_active(self) -> bool:
        """Return a synchronized point-in-time liveness result."""

        return self._book._contains(self._token)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()

    def release(self) -> None:
        """Release only this reservation; repeated calls are harmless."""

        if self._closed:
            return
        self._closed = True
        self._book._release(self._token)


class QuotaReservationBook:
    """Serialize reservations within the trusted storage broker process."""

    def __init__(self, quota: WorkspaceQuota) -> None:
        self._quota = quota
        self._lock = Lock()
        self._reservations: dict[UUID, _ReservedCapacity] = {}
        self._observations: dict[UUID, _QuotaReservationObservationState] = {}

    @property
    def quota(self) -> WorkspaceQuota:
        """Return the immutable policy bound to this reservation book."""

        return self._quota

    @property
    def has_reservations(self) -> bool:
        """Return whether any capacity claim remains active."""

        with self._lock:
            return bool(self._reservations)

    def has_reservations_for(self, area: QuotaArea) -> bool:
        """Return whether the selected disposable area has an active claim."""

        with self._lock:
            return any(item.area is area for item in self._reservations.values())

    @property
    def has_observations(self) -> bool:
        """Return whether the one bounded telemetry observation is active."""

        with self._lock:
            return bool(self._observations)

    @property
    def reserved_byte_count(self) -> int:
        """Return the synchronized total bytes conservatively claimed."""

        with self._lock:
            return self._snapshot_locked().case_bytes

    @property
    def snapshot(self) -> QuotaReservationSnapshot:
        """Return content-free active claims from one lock acquisition."""

        with self._lock:
            return self._snapshot_locked()

    def observe(self) -> QuotaReservationObservation:
        """Open an event-driven observation that cannot miss a brief claim."""

        with self._lock:
            if self._observations:
                reject(
                    WorkspaceErrorCode.QUOTA_INVALID,
                    "quota.observation",
                    "Only one bounded workspace-capacity observation may be active.",
                )
            token = uuid4()
            while token in self._observations:
                token = uuid4()
            self._observations[token] = _QuotaReservationObservationState.from_snapshot(
                self._snapshot_locked()
            )
        return QuotaReservationObservation(self, token)

    def reserve(
        self,
        usage: WorkspaceUsage,
        area: QuotaArea,
        *,
        byte_count: int,
        entry_count: int = 1,
    ) -> QuotaReservation:
        """Reserve capacity atomically against measured use and outstanding claims."""

        validate_quota_request(byte_count=byte_count, entry_count=entry_count)
        with self._lock:
            reserved = self._snapshot_locked()
            projected_usage = WorkspaceUsage(
                case_bytes=usage.case_bytes + reserved.case_bytes,
                cache_bytes=usage.cache_bytes + reserved.cache_bytes,
                temp_bytes=usage.temp_bytes + reserved.temp_bytes,
                entry_count=usage.entry_count + reserved.entry_count,
                maximum_depth_seen=usage.maximum_depth_seen,
            )
            require_capacity(
                self._quota,
                projected_usage,
                area,
                byte_count=byte_count,
                entry_count=entry_count,
            )
            token = uuid4()
            while token in self._reservations:
                token = uuid4()
            capacity = _ReservedCapacity(area, byte_count, entry_count)
            self._reservations[token] = capacity
            snapshot = self._snapshot_locked()
            for observation in self._observations.values():
                observation.record(snapshot)
        return QuotaReservation(self, token, capacity)

    def _contains(self, token: UUID) -> bool:
        with self._lock:
            return token in self._reservations

    def _release(self, token: UUID) -> None:
        with self._lock:
            self._reservations.pop(token, None)

    def _snapshot_locked(self) -> QuotaReservationSnapshot:
        cache_bytes = 0
        temp_bytes = 0
        entry_count = 0
        for item in self._reservations.values():
            entry_count += item.entry_count
            if item.area is QuotaArea.CACHE:
                cache_bytes += item.byte_count
            else:
                temp_bytes += item.byte_count
        return QuotaReservationSnapshot(
            case_bytes=cache_bytes + temp_bytes,
            cache_bytes=cache_bytes,
            temp_bytes=temp_bytes,
            entry_count=entry_count,
        )

    def _observation_snapshot(
        self,
        token: UUID,
    ) -> QuotaReservationObservationSnapshot:
        with self._lock:
            return self._observations[token].measurement(self._snapshot_locked())

    def _end_observation(
        self,
        token: UUID,
    ) -> QuotaReservationObservationSnapshot:
        with self._lock:
            state = self._observations.pop(token)
            return state.measurement(self._snapshot_locked())


def validate_quota_request(*, byte_count: int, entry_count: int) -> None:
    """Reject malformed capacity values before an expensive workspace inventory."""

    if (
        type(byte_count) is not int
        or type(entry_count) is not int
        or not 0 <= byte_count <= MAX_SAFE_QUOTA_INTEGER
        or not 0 <= entry_count <= MAX_SAFE_QUOTA_INTEGER
        or (byte_count == 0 and entry_count == 0)
    ):
        reject(
            WorkspaceErrorCode.QUOTA_INVALID,
            "quota.request",
            "A quota request must contain bounded non-negative capacity.",
        )


def require_capacity(
    quota: WorkspaceQuota,
    usage: WorkspaceUsage,
    area: QuotaArea,
    *,
    byte_count: int,
    entry_count: int = 1,
) -> None:
    """Fail when adding a request would cross a configured inclusive maximum."""

    validate_quota_request(byte_count=byte_count, entry_count=entry_count)
    if type(area) is not QuotaArea:
        reject(
            WorkspaceErrorCode.QUOTA_INVALID,
            "quota.area",
            "Capacity may be reserved only for cache or temporary storage.",
        )
    if (
        usage.case_bytes > quota.maximum_case_bytes - byte_count
        or usage.entry_count > quota.maximum_entries - entry_count
    ):
        reject(
            WorkspaceErrorCode.QUOTA_EXCEEDED,
            "quota.case",
            "The case does not have enough configured storage capacity.",
        )
    used_area_bytes = usage.cache_bytes if area is QuotaArea.CACHE else usage.temp_bytes
    maximum_area_bytes = (
        quota.maximum_cache_bytes if area is QuotaArea.CACHE else quota.maximum_temp_bytes
    )
    if used_area_bytes > maximum_area_bytes - byte_count:
        reject(
            WorkspaceErrorCode.QUOTA_EXCEEDED,
            f"quota.{area.value}",
            "The selected case area does not have enough configured storage capacity.",
        )

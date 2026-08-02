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


class QuotaReservation(AbstractContextManager["QuotaReservation"]):
    """A conservative in-process capacity claim released explicitly or on exit."""

    __slots__ = ("_book", "_closed", "_token")

    def __init__(self, book: QuotaReservationBook, token: UUID) -> None:
        self._book = book
        self._closed = False
        self._token = token

    @property
    def token(self) -> UUID:
        return self._token

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

    @property
    def quota(self) -> WorkspaceQuota:
        """Return the immutable policy bound to this reservation book."""

        return self._quota

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
            reserved_case_bytes = sum(item.byte_count for item in self._reservations.values())
            reserved_entries = sum(item.entry_count for item in self._reservations.values())
            reserved_cache_bytes = sum(
                item.byte_count
                for item in self._reservations.values()
                if item.area is QuotaArea.CACHE
            )
            reserved_temp_bytes = sum(
                item.byte_count
                for item in self._reservations.values()
                if item.area is QuotaArea.TEMP
            )
            projected_usage = WorkspaceUsage(
                case_bytes=usage.case_bytes + reserved_case_bytes,
                cache_bytes=usage.cache_bytes + reserved_cache_bytes,
                temp_bytes=usage.temp_bytes + reserved_temp_bytes,
                entry_count=usage.entry_count + reserved_entries,
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
            self._reservations[token] = _ReservedCapacity(area, byte_count, entry_count)
        return QuotaReservation(self, token)

    def _release(self, token: UUID) -> None:
        with self._lock:
            self._reservations.pop(token, None)


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

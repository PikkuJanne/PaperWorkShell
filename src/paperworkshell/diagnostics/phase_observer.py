"""Private, opt-in timing for one known synthetic PNG import."""

from __future__ import annotations

import ctypes
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum
from typing import Final
from uuid import UUID

ENGINEERING_CANDIDATE: Final = "pws0811-engineering-image-preview-v11"
_FIXTURE_SHA256: Final = "f2976a85d04b992bd690d8a2b70faf8444197d87ce58a7a5687a778c97f3fd82"
_FIXTURE_BYTES: Final = 21_992
_LIMIT_SECONDS: Final = 120
_MAX_EVENTS: Final = 32
_MAX_BYTES: Final = 8_192


class Event(StrEnum):
    IMPORT_DISPATCH = "import_dispatch"
    LAUNCH_ENTER = "launch_enter"
    FIXED_GRANT_ENTER = "fixed_grant_enter"
    FIXED_GRANT_OK = "fixed_grant_ok"
    LAUNCH_OK = "launch_ok"
    READY_VALIDATED = "ready_validated"
    INSPECTION_VALIDATED = "inspection_validated"
    DURABLE_COMMIT_VERIFIED = "durable_commit_verified"
    IMPORT_REFRESHED = "import_refreshed"
    PREVIEW_DISPATCH = "preview_dispatch"
    INITIAL_PREVIEW_REUSED = "initial_preview_reused"
    TRANSFORM_CACHE_MISS = "transform_cache_miss"
    RETAINED_RECOVERY_ENTER = "retained_recovery_enter"
    RETAINED_RECOVERY_OK = "retained_recovery_ok"
    RETAINED_INVENTORY_ENTER = "retained_inventory_enter"
    RETAINED_INVENTORY_OK = "retained_inventory_ok"
    PREVIEW_ACCEPTED = "preview_accepted"


class Role(StrEnum):
    INSPECTION = "inspection"
    PREVIEW = "preview"


_EXPECTED_PREFIX: Final = (
    (Event.IMPORT_DISPATCH, None),
    (Event.LAUNCH_ENTER, Role.INSPECTION),
    (Event.FIXED_GRANT_ENTER, Role.INSPECTION),
    (Event.FIXED_GRANT_OK, Role.INSPECTION),
    (Event.LAUNCH_OK, Role.INSPECTION),
    (Event.READY_VALIDATED, Role.INSPECTION),
    (Event.INSPECTION_VALIDATED, None),
    (Event.DURABLE_COMMIT_VERIFIED, None),
    (Event.IMPORT_REFRESHED, None),
    (Event.PREVIEW_DISPATCH, None),
)
_EXPECTED_INITIAL_PREVIEW: Final = (
    *_EXPECTED_PREFIX,
    (Event.INITIAL_PREVIEW_REUSED, None),
    (Event.PREVIEW_ACCEPTED, None),
)
_EXPECTED: Final = (
    *_EXPECTED_PREFIX,
    (Event.TRANSFORM_CACHE_MISS, None),
    (Event.LAUNCH_ENTER, Role.PREVIEW),
    (Event.RETAINED_RECOVERY_ENTER, Role.PREVIEW),
    (Event.RETAINED_RECOVERY_OK, Role.PREVIEW),
    (Event.RETAINED_INVENTORY_ENTER, Role.PREVIEW),
    (Event.RETAINED_INVENTORY_OK, Role.PREVIEW),
    (Event.LAUNCH_OK, Role.PREVIEW),
    (Event.READY_VALIDATED, Role.PREVIEW),
    (Event.PREVIEW_ACCEPTED, None),
)


def windows_clock() -> tuple[Callable[[], int], int]:
    """Read the same native QPC domain used by the independent capture helper."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    counter = kernel.QueryPerformanceCounter
    frequency = kernel.QueryPerformanceFrequency
    for function in (counter, frequency):
        function.argtypes = (ctypes.POINTER(ctypes.c_int64),)
        function.restype = ctypes.c_int
    value = ctypes.c_int64()
    if not frequency(ctypes.byref(value)) or value.value <= 0:
        raise OSError("QPC unavailable")

    def read() -> int:
        ticks = ctypes.c_int64()
        if not counter(ctypes.byref(ticks)):
            raise OSError("QPC unavailable")
        return ticks.value

    return read, value.value


class Observation:
    """Keep exact bindings in memory and serialize only closed timing rows."""

    def __init__(
        self,
        case_id: UUID,
        revision: int,
        clock: Callable[[], int],
        frequency: int,
    ) -> None:
        self.case_id = case_id
        self.original_revision = revision
        self._clock = clock
        self._frequency = frequency
        self._arm_ticks = clock()
        self._origin: int | None = None
        self.import_id: int | None = None
        self.preview_id: tuple[int, int] | None = None
        self.source_id: UUID | None = None
        self.page_id: UUID | None = None
        self.revision: int | None = None
        self.events: list[tuple[Event, Role | None, int]] = []
        self._expected: tuple[tuple[Event, Role | None], ...] = _EXPECTED
        self._worker: int | None = None
        self._launch: tuple[Role, int, int] | None = None
        self._first_generation: int | None = None
        self._inventory: tuple[int, int] | None = None
        self.qualified = False
        self.terminal = False
        self.complete = False

    def record(self, event: Event, role: Role | None = None) -> None:
        if self.terminal:
            return
        try:
            self._record(event, role)
        except Exception:
            self.terminal = True

    def _record(self, event: Event, role: Role | None) -> None:
        index = len(self.events)
        if index >= min(_MAX_EVENTS, len(self._expected)) or self._expected[index] != (event, role):
            self.terminal = True
            return
        now = self._clock()
        if self.terminal:
            return
        if self._origin is None:
            if not 0 <= now - self._arm_ticks <= _LIMIT_SECONDS * self._frequency:
                self.terminal = True
                return
            self._origin = now
        elapsed = now - self._origin
        previous = self.events[-1][2] if self.events else 0
        if not previous <= elapsed <= _LIMIT_SECONDS * self._frequency:
            self.terminal = True
            return
        self.events.append((event, role, elapsed))
        if event is Event.PREVIEW_ACCEPTED:
            self.complete = True
            self.terminal = True

    def dispatch(self, operation_id: int, case_id: UUID | None, revision: int | None) -> None:
        if self.terminal or self.import_id is not None:
            return
        if case_id != self.case_id or revision != self.original_revision:
            self.terminal = True
            return
        self.import_id = operation_id
        self.record(Event.IMPORT_DISPATCH)

    def qualify(self, case_id: UUID, source_id: UUID, media: str, size: int, digest: str) -> None:
        if (
            not self.terminal
            and case_id == self.case_id
            and media == "image/png"
            and size == _FIXTURE_BYTES
            and digest == _FIXTURE_SHA256
        ):
            self.source_id = source_id
            self.qualified = True
        else:
            self.events.clear()
            self.terminal = True

    def import_refreshed(
        self, case_id: UUID | None, source_id: UUID, revision: int | None, page_id: UUID
    ) -> None:
        if self.terminal or not self.qualified:
            return
        if (
            self.case_id != case_id
            or self.source_id != source_id
            or revision is None
            or revision <= self.original_revision
        ):
            self.terminal = True
            return
        self.revision = revision
        self.page_id = page_id
        self.record(Event.IMPORT_REFRESHED)

    def preview_dispatch(
        self,
        operation_id: int,
        generation: int,
        case_id: UUID | None,
        revision: int | None,
        page_ids: tuple[UUID, ...],
        selected_page: UUID | None,
    ) -> None:
        if self.terminal or self.page_id is None or self.preview_id is not None:
            return
        if (
            case_id != self.case_id
            or revision != self.revision
            or page_ids != (self.page_id,)
            or selected_page != self.page_id
        ):
            self.terminal = True
            return
        self.preview_id = operation_id, generation
        self.record(Event.PREVIEW_DISPATCH)

    def preview_accepted(
        self,
        operation_id: int,
        generation: int,
        case_id: UUID,
        source_id: UUID,
        page_id: UUID,
        revision: int | None,
        selected_page: UUID | None,
    ) -> None:
        if self.terminal or self.preview_id != (operation_id, generation):
            return
        if (
            case_id != self.case_id
            or source_id != self.source_id
            or page_id != self.page_id
            or revision != self.revision
            or selected_page != self.page_id
        ):
            self.terminal = True
            return
        self.record(Event.PREVIEW_ACCEPTED)

    def initial_preview_reused(
        self, case_id: UUID, source_id: UUID, page_id: UUID, revision: int
    ) -> None:
        if self.terminal:
            return
        if (
            not self.qualified
            or self.preview_id is None
            or len(self.events) != len(_EXPECTED_PREFIX)
            or case_id != self.case_id
            or source_id != self.source_id
            or page_id != self.page_id
            or revision != self.revision
        ):
            self.terminal = True
            return
        self._expected = _EXPECTED_INITIAL_PREVIEW
        self.record(Event.INITIAL_PREVIEW_REUSED)

    def initial_preview_accepted(
        self,
        operation_id: int,
        generation: int,
        case_id: UUID,
        page_id: UUID,
        revision: int | None,
        selected_page: UUID | None,
    ) -> None:
        if self._expected is not _EXPECTED_INITIAL_PREVIEW or self.source_id is None:
            self.terminal = True
            return
        self.preview_accepted(
            operation_id, generation, case_id, self.source_id, page_id, revision, selected_page
        )

    def launch(
        self, role: Role, client: object, generation: int, case_id: UUID, request: object
    ) -> None:
        if not self.qualified or self.terminal:
            return
        worker = id(client)
        if case_id != self.case_id or self._worker not in (None, worker):
            self.terminal = True
            return
        if role is Role.INSPECTION:
            self._first_generation = generation
        elif self._first_generation is None or generation <= self._first_generation:
            self.terminal = True
            return
        self._worker = worker
        self._launch = role, generation, id(request)
        self.record(Event.LAUNCH_ENTER, role)

    def client_event(self, role: Role, client: object, generation: int, event: Event) -> None:
        if (
            self._worker != id(client)
            or self._launch is None
            or self._launch[:2]
            != (
                role,
                generation,
            )
        ):
            self.terminal = True
            return
        self.record(event, role)

    def launcher_event(
        self,
        role: Role,
        request: object,
        owner: object,
        event: Event,
        inventory: object = None,
    ) -> None:
        if self.terminal or self._launch is None:
            return
        if self._launch[0] is not role or self._launch[2] != id(request) or owner is None:
            self.terminal = True
            return
        if event is Event.FIXED_GRANT_OK:
            if inventory is None:
                self.terminal = True
                return
            self._inventory = id(owner), id(inventory)
        if role is Role.PREVIEW and (
            self._inventory is None
            or self._inventory[0] != id(owner)
            or (
                event in (Event.RETAINED_INVENTORY_ENTER, Event.RETAINED_INVENTORY_OK)
                and self._inventory[1] != id(inventory)
            )
        ):
            self.terminal = True
            return
        self.record(event, role)

    def receipt(self) -> bytes | None:
        if not self.qualified or self._origin is None:
            return None
        self.terminal = True
        events: list[dict[str, str | int]] = []
        for ordinal, (event, role, ticks) in enumerate(self.events, start=1):
            row: dict[str, str | int] = {"ordinal": ordinal, "event": event, "ticks": ticks}
            if role is not None:
                row["role"] = role
                row["generation"] = 1 if role is Role.INSPECTION else 2
            events.append(row)
        payload = {
            "schema_version": 1,
            "candidate": ENGINEERING_CANDIDATE,
            "action": 1,
            "status": "complete" if self.complete else "incomplete",
            "clock": {
                "domain": "windows-qpc",
                "origin": self._origin,
                "frequency": self._frequency,
            },
            "events": events,
        }
        encoded = (json.dumps(payload, separators=(",", ":")) + "\n").encode("ascii")
        return encoded if len(encoded) <= _MAX_BYTES else None


active: Observation | None = None
_backend_scope: ContextVar[tuple[Observation, Role] | None] = ContextVar(
    "phase_observer_backend_scope", default=None
)


@contextmanager
def backend_scope(observation: Observation, role: Role) -> Iterator[None]:
    token = _backend_scope.set((observation, role))
    try:
        yield
    finally:
        _backend_scope.reset(token)


def current_scope() -> tuple[Observation, Role] | None:
    if active is None:
        return None
    scope = _backend_scope.get()
    return scope if scope is not None and scope[0] is active and not scope[0].terminal else None

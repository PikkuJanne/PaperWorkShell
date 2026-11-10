"""The private recorder seals only correctly bound synthetic phases."""

from __future__ import annotations

import json
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest

from paperworkshell.diagnostics import phase_observer
from paperworkshell.diagnostics.phase_observer import Event, Observation, Role

_FIXTURE_DIGEST = "f2976a85d04b992bd690d8a2b70faf8444197d87ce58a7a5687a778c97f3fd82"


def _ticks() -> Iterator[int]:
    yield from range(100, 1_000)


def _dispatch() -> tuple[Observation, UUID]:
    ticks = _ticks()
    case_id, source_id = uuid4(), uuid4()
    observation = Observation(case_id, 0, lambda: next(ticks), 100)
    observation.dispatch(37, case_id, 0)
    return observation, source_id


def _qualify(observation: Observation, source_id: UUID) -> None:
    observation.qualify(observation.case_id, source_id, "image/png", 21_992, _FIXTURE_DIGEST)


def test_sanitized_sequence_requires_distinct_worker_generations_and_current_page() -> None:
    observation, source_id = _dispatch()
    _qualify(observation, source_id)
    page_id = uuid4()
    worker, request, owner, inventory = object(), object(), object(), object()
    observation.launch(Role.INSPECTION, worker, 41, observation.case_id, request)
    observation.launcher_event(Role.INSPECTION, request, owner, Event.FIXED_GRANT_ENTER)
    observation.launcher_event(Role.INSPECTION, request, owner, Event.FIXED_GRANT_OK, inventory)
    observation.client_event(Role.INSPECTION, worker, 41, Event.LAUNCH_OK)
    observation.client_event(Role.INSPECTION, worker, 41, Event.READY_VALIDATED)
    observation.record(Event.INSPECTION_VALIDATED)
    observation.record(Event.DURABLE_COMMIT_VERIFIED)
    observation.import_refreshed(observation.case_id, source_id, 1, page_id)
    observation.preview_dispatch(38, 9, observation.case_id, 1, (page_id,), page_id)
    observation.record(Event.TRANSFORM_CACHE_MISS)
    preview_request = object()
    observation.launch(Role.PREVIEW, worker, 42, observation.case_id, preview_request)
    observation.launcher_event(Role.PREVIEW, preview_request, owner, Event.RETAINED_RECOVERY_ENTER)
    observation.launcher_event(Role.PREVIEW, preview_request, owner, Event.RETAINED_RECOVERY_OK)
    observation.launcher_event(
        Role.PREVIEW, preview_request, owner, Event.RETAINED_INVENTORY_ENTER, inventory
    )
    observation.launcher_event(
        Role.PREVIEW, preview_request, owner, Event.RETAINED_INVENTORY_OK, inventory
    )
    observation.client_event(Role.PREVIEW, worker, 42, Event.LAUNCH_OK)
    observation.client_event(Role.PREVIEW, worker, 42, Event.READY_VALIDATED)
    observation.preview_accepted(38, 8, observation.case_id, source_id, page_id, 1, page_id)
    assert not observation.complete
    observation.preview_accepted(38, 9, observation.case_id, source_id, page_id, 1, page_id)

    encoded = observation.receipt()
    assert encoded is not None
    assert len(encoded) <= 8_192
    for prohibited in (str(observation.case_id), str(source_id), str(page_id), _FIXTURE_DIGEST):
        assert prohibited.encode("ascii") not in encoded
    receipt = json.loads(encoded)
    assert receipt["status"] == "complete"
    assert receipt["clock"] == {"domain": "windows-qpc", "origin": 101, "frequency": 100}
    assert [(row["event"], row.get("role")) for row in receipt["events"]] == list(
        phase_observer._EXPECTED
    )
    assert {row["generation"] for row in receipt["events"] if "generation" in row} == {1, 2}


def test_wrong_synthetic_fixture_discards_provisional_timing() -> None:
    observation, source_id = _dispatch()
    observation.qualify(observation.case_id, source_id, "image/png", 21_992, "0" * 64)
    assert observation.terminal
    assert not observation.events
    assert observation.receipt() is None


@pytest.mark.parametrize("bad_request", [False, True])
def test_unbound_or_failed_grant_cannot_record_success(bad_request: bool) -> None:
    observation, source_id = _dispatch()
    _qualify(observation, source_id)
    worker, request, owner = object(), object(), object()
    observation.launch(Role.INSPECTION, worker, 1, observation.case_id, request)
    observation.launcher_event(Role.INSPECTION, request, owner, Event.FIXED_GRANT_ENTER)
    if bad_request:
        observation.launcher_event(Role.INSPECTION, object(), owner, Event.FIXED_GRANT_OK, object())
    observation.client_event(Role.INSPECTION, worker, 1, Event.LAUNCH_OK)
    encoded = observation.receipt()
    assert encoded is not None
    receipt = json.loads(encoded)
    assert receipt["status"] == "incomplete"
    assert [row["event"] for row in receipt["events"]] == [
        "import_dispatch",
        "launch_enter",
        "fixed_grant_enter",
    ]


def test_expired_arm_never_becomes_a_measurement() -> None:
    ticks = iter((0, 12_001))
    case_id = uuid4()
    observation = Observation(case_id, 0, lambda: next(ticks), 100)
    observation.dispatch(1, case_id, 0)
    _qualify(observation, uuid4())
    assert observation.receipt() is None


def test_scope_requires_explicit_active_observation(monkeypatch: pytest.MonkeyPatch) -> None:
    observation, _source_id = _dispatch()
    assert phase_observer.current_scope() is None
    with phase_observer.backend_scope(observation, Role.INSPECTION):
        assert phase_observer.current_scope() is None
        monkeypatch.setattr(phase_observer, "active", observation)
        assert phase_observer.current_scope() == (observation, Role.INSPECTION)
    assert phase_observer.current_scope() is None

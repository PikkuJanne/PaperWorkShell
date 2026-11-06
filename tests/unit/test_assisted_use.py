"""Deterministic assisted consent, unlock, expiry, restart and isolation coverage."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event, Thread
from uuid import UUID

import pytest

from paperworkshell.app import assisted_use as assisted_application
from paperworkshell.app.assisted_use import AssistedUseService, AssistedUseStore
from paperworkshell.domain.assisted_use import (
    ASSISTED_USE_IDLE_LOCK_SECONDS,
    ASSISTED_USE_UNLOCK_ATTEMPTS,
    ASSISTED_USE_UNLOCK_COOLDOWN_SECONDS,
    AssistedUseError,
    AssistedUseErrorCode,
    AssistedUseRecord,
    AssistedUseSnapshot,
    AssistedUseStatus,
    record_from_document,
    record_to_document,
)

CASE = UUID("10000000-0000-4000-8000-000000000001")
OTHER = UUID("20000000-0000-4000-8000-000000000002")
NOW = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
PASSPHRASE = "synthetic assisted unlock phrase"


class Store:
    def __init__(self) -> None:
        self.records: dict[UUID, AssistedUseRecord] = {}
        self.fail_load = False
        self.fail_save = False
        self.saves = 0

    def load(self, case_id: UUID) -> AssistedUseRecord | None:
        if self.fail_load:
            raise OSError("PRIVATE_VALUE C:\\PRIVATE_PATH\\document.pdf")
        return self.records.get(case_id)

    def save(self, record: AssistedUseRecord) -> None:
        if self.fail_save:
            raise OSError("PRIVATE_VALUE C:\\PRIVATE_PATH\\document.pdf")
        self.records[record.case_id] = record
        self.saves += 1


class Clock:
    def __init__(self) -> None:
        self.now = NOW
        self.tick = 1000.0

    def wall(self) -> datetime:
        return self.now

    def monotonic(self) -> float:
        return self.tick

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)
        self.tick += seconds


@pytest.fixture
def setup() -> tuple[AssistedUseService, Store, Clock]:
    store, clock = Store(), Clock()
    service = AssistedUseService(store, clock=clock.wall, monotonic_clock=clock.monotonic)
    return service, store, clock


def enroll(service: AssistedUseService) -> AssistedUseSnapshot:
    return service.enroll(CASE, 7, PASSPHRASE, consent=True)


def test_store_contract_and_ordinary_inspection(
    setup: tuple[AssistedUseService, Store, Clock],
) -> None:
    service, store, _ = setup
    assert isinstance(store, AssistedUseStore)
    assert service.inspect(CASE) == AssistedUseSnapshot(
        CASE, AssistedUseStatus.ORDINARY, None, None
    )
    assert service.snapshot is None
    assert service.check() is None
    service.require_access(CASE)
    assert store.saves == 0


@pytest.mark.parametrize("consent", [False, None, 1, "yes"])
def test_consent_must_be_exact_and_writes_nothing(
    setup: tuple[AssistedUseService, Store, Clock], consent: object
) -> None:
    service, store, _ = setup
    with pytest.raises(AssistedUseError) as raised:
        service.enroll(CASE, 7, PASSPHRASE, consent=consent)  # type: ignore[arg-type]
    assert raised.value.code is AssistedUseErrorCode.CONSENT_REQUIRED
    assert store.saves == 0
    assert service.snapshot is None


@pytest.mark.parametrize("days", [0, 2, 31, True, "7"])
def test_retention_is_closed_and_noncoercible(
    setup: tuple[AssistedUseService, Store, Clock], days: object
) -> None:
    service, store, _ = setup
    with pytest.raises(AssistedUseError):
        service.enroll(CASE, days, PASSPHRASE, consent=True)  # type: ignore[arg-type]
    assert store.saves == 0


@pytest.mark.parametrize("phrase", ["short", " " * 12, "x" * 129, "\ud800" * 12, None])
def test_bad_passphrases_do_not_persist(
    setup: tuple[AssistedUseService, Store, Clock], phrase: object
) -> None:
    service, store, _ = setup
    with pytest.raises(AssistedUseError) as raised:
        service.enroll(CASE, 7, phrase, consent=True)  # type: ignore[arg-type]
    assert raised.value.code is AssistedUseErrorCode.PASSPHRASE_INVALID
    assert store.saves == 0


@pytest.mark.parametrize("days", [1, 7, 30])
def test_enrollment_records_only_closed_consent_and_fixed_deadline(
    setup: tuple[AssistedUseService, Store, Clock], days: int
) -> None:
    service, store, _ = setup
    snapshot = service.enroll(CASE, days, PASSPHRASE, consent=True)
    assert snapshot.status is AssistedUseStatus.UNLOCKED
    assert snapshot.expires_at == NOW + timedelta(days=days)
    record = store.records[CASE]
    assert record.consented_at == NOW
    assert record.last_seen_at == NOW
    assert record.consent_version == 1
    assert PASSPHRASE not in repr(record)
    assert record.salt.hex() not in repr(record)
    assert record.verifier.hex() not in repr(record)
    assert PASSPHRASE not in str(record_to_document(record))
    service.require_access(CASE)


def test_salt_changes_verifier_for_same_phrase(
    setup: tuple[AssistedUseService, Store, Clock],
) -> None:
    service, store, _ = setup
    enroll(service)
    service.release()
    service.enroll(OTHER, 7, PASSPHRASE, consent=True)
    assert store.records[CASE].salt != store.records[OTHER].salt
    assert store.records[CASE].verifier != store.records[OTHER].verifier


def test_manual_lock_is_memory_only_and_requires_local_unlock(
    setup: tuple[AssistedUseService, Store, Clock],
) -> None:
    service, store, _ = setup
    enroll(service)
    store.fail_load = store.fail_save = True
    snapshot = service.lock()
    assert snapshot is not None and snapshot.status is AssistedUseStatus.LOCKED
    assert store.saves == 1
    with pytest.raises(AssistedUseError) as raised:
        service.require_access(CASE)
    assert raised.value.code is AssistedUseErrorCode.LOCKED
    store.fail_load = store.fail_save = False
    assert service.unlock(CASE, PASSPHRASE).status is AssistedUseStatus.UNLOCKED


def test_restart_always_requires_unlock_even_after_crash(
    setup: tuple[AssistedUseService, Store, Clock],
) -> None:
    original, store, clock = setup
    enroll(original)
    restarted = AssistedUseService(store, clock=clock.wall, monotonic_clock=clock.monotonic)
    assert restarted.inspect(CASE).status is AssistedUseStatus.LOCKED
    assert restarted.snapshot is not None
    assert restarted.snapshot.status is AssistedUseStatus.LOCKED
    with pytest.raises(AssistedUseError) as raised:
        restarted.require_access(CASE)
    assert raised.value.code is AssistedUseErrorCode.LOCKED
    assert restarted.unlock(CASE, PASSPHRASE).status is AssistedUseStatus.UNLOCKED


def test_idle_lock_counts_monotonic_time_and_input_cannot_revive_it(
    setup: tuple[AssistedUseService, Store, Clock],
) -> None:
    service, store, clock = setup
    enroll(service)
    clock.tick += ASSISTED_USE_IDLE_LOCK_SECONDS
    with pytest.raises(AssistedUseError) as raised:
        service.activity()
    assert raised.value.code is AssistedUseErrorCode.LOCKED
    assert service.snapshot is not None and service.snapshot.status is AssistedUseStatus.LOCKED
    assert store.saves == 1


def test_activity_is_memory_only_and_resets_idle_without_extending_expiry(
    setup: tuple[AssistedUseService, Store, Clock],
) -> None:
    service, store, clock = setup
    enrolled = enroll(service)
    clock.tick += 299
    store.fail_load = store.fail_save = True
    assert service.activity() == enrolled
    clock.tick += 299
    assert service.activity() == enrolled
    assert store.saves == 1


def test_scheduler_detects_idle_and_checkpoints_at_most_once_a_minute(
    setup: tuple[AssistedUseService, Store, Clock],
) -> None:
    service, store, clock = setup
    enroll(service)
    clock.advance(59)
    service.check()
    assert store.saves == 1
    clock.advance(1)
    service.check()
    assert store.saves == 2
    clock.advance(240)
    snapshot = service.check()
    assert snapshot is not None and snapshot.status is AssistedUseStatus.LOCKED


def test_unlock_durably_checkpoints_a_recent_inmemory_highwater(
    setup: tuple[AssistedUseService, Store, Clock],
) -> None:
    service, store, clock = setup
    enroll(service)
    clock.advance(59)
    service.check()
    assert store.records[CASE].last_seen_at == NOW
    service.lock()
    service.unlock(CASE, PASSPHRASE)
    assert store.records[CASE].last_seen_at == clock.now


def test_forward_expiry_is_sticky_across_clock_rollback_and_restart(
    setup: tuple[AssistedUseService, Store, Clock],
) -> None:
    service, store, clock = setup
    enrolled = enroll(service)
    clock.advance(7 * 24 * 60 * 60)
    expired = service.check()
    assert expired is not None and expired.status is AssistedUseStatus.EXPIRED
    assert store.records[CASE].expired is True
    clock.now = NOW
    assert service.check() == expired
    service.release()
    restarted = AssistedUseService(store, clock=clock.wall, monotonic_clock=clock.monotonic)
    assert restarted.inspect(CASE).status is AssistedUseStatus.EXPIRED
    with pytest.raises(AssistedUseError) as raised:
        restarted.unlock(CASE, PASSPHRASE)
    assert raised.value.code is AssistedUseErrorCode.EXPIRED
    assert expired.expires_at == enrolled.expires_at
    assert CASE in store.records


def test_backwards_clock_locks_until_corrected_and_never_changes_deadline(
    setup: tuple[AssistedUseService, Store, Clock],
) -> None:
    service, store, clock = setup
    deadline = enroll(service).expires_at
    clock.advance(60)
    service.check()
    clock.now = NOW
    snapshot = service.check()
    assert snapshot is not None and snapshot.status is AssistedUseStatus.CLOCK_REVIEW
    with pytest.raises(AssistedUseError) as raised:
        service.unlock(CASE, PASSPHRASE)
    assert raised.value.code is AssistedUseErrorCode.CLOCK_REVIEW
    clock.now = NOW + timedelta(seconds=60)
    assert service.check() == AssistedUseSnapshot(CASE, AssistedUseStatus.LOCKED, deadline, 7)
    assert service.unlock(CASE, PASSPHRASE).expires_at == deadline
    assert store.records[CASE].expires_at == deadline


@pytest.mark.parametrize("action", ["inspect", "unlock", "enroll", "require_access", "lock"])
def test_one_active_assisted_case_rejects_unrelated_case_operations(
    setup: tuple[AssistedUseService, Store, Clock], action: str
) -> None:
    service, store, _ = setup
    enroll(service)
    with pytest.raises(AssistedUseError) as raised:
        if action == "inspect":
            service.inspect(OTHER)
        elif action == "unlock":
            service.unlock(OTHER, PASSPHRASE)
        elif action == "enroll":
            service.enroll(OTHER, 1, PASSPHRASE, consent=True)
        elif action == "require_access":
            service.require_access(OTHER)
        else:
            service.lock(OTHER)
    assert raised.value.code is AssistedUseErrorCode.CASE_CONFLICT
    assert OTHER not in store.records


def test_wrong_unlock_is_sanitized_and_throttled_without_sleep(
    setup: tuple[AssistedUseService, Store, Clock],
) -> None:
    service, _, clock = setup
    enroll(service)
    service.lock()
    for _ in range(ASSISTED_USE_UNLOCK_ATTEMPTS):
        with pytest.raises(AssistedUseError) as raised:
            service.unlock(CASE, "wrong synthetic phrase")
        assert raised.value.code is AssistedUseErrorCode.UNLOCK_FAILED
        assert "wrong synthetic" not in repr(raised.value)
    with pytest.raises(AssistedUseError) as raised:
        service.unlock(CASE, PASSPHRASE)
    assert raised.value.code is AssistedUseErrorCode.UNLOCK_WAIT
    clock.advance(ASSISTED_USE_UNLOCK_COOLDOWN_SECONDS)
    assert service.unlock(CASE, PASSPHRASE).status is AssistedUseStatus.UNLOCKED


def test_failure_path_locks_and_preserves_active_selection(
    setup: tuple[AssistedUseService, Store, Clock],
) -> None:
    service, store, clock = setup
    enroll(service)
    store.fail_save = True
    clock.advance(60)
    with pytest.raises(AssistedUseError) as raised:
        service.check()
    assert raised.value.code is AssistedUseErrorCode.STORAGE_FAILED
    assert "PRIVATE" not in str(raised.value)
    assert "PRIVATE" not in repr(raised.value)
    assert raised.value.__suppress_context__
    assert service.snapshot is not None and service.snapshot.status is AssistedUseStatus.LOCKED
    with pytest.raises(AssistedUseError):
        service.release()
    assert service.snapshot is not None


def test_corrupt_store_and_load_failure_are_closed_errors(
    setup: tuple[AssistedUseService, Store, Clock],
) -> None:
    service, store, _ = setup
    store.fail_load = True
    with pytest.raises(AssistedUseError) as raised:
        service.inspect(CASE)
    assert raised.value.code is AssistedUseErrorCode.STORAGE_FAILED
    assert "PRIVATE" not in str(raised.value)
    store.fail_load = False
    store.records[CASE] = object()  # type: ignore[assignment]
    with pytest.raises(AssistedUseError) as raised:
        service.inspect(CASE)
    assert raised.value.code is AssistedUseErrorCode.INVALID


def test_current_record_roundtrip_and_released_reopen(
    setup: tuple[AssistedUseService, Store, Clock],
) -> None:
    service, store, _ = setup
    enroll(service)
    record = store.records[CASE]
    assert record_from_document(record_to_document(record)) == record
    service.release()
    assert service.snapshot is None
    assert service.inspect(CASE).status is AssistedUseStatus.LOCKED


@pytest.mark.parametrize("load_failure", (False, True))
def test_scheduler_check_never_reselects_a_released_case(
    setup: tuple[AssistedUseService, Store, Clock], load_failure: bool
) -> None:
    service, store, _ = setup
    enroll(service)
    service.release()
    store.fail_load = load_failure
    assert service.check(CASE) is None
    assert service.snapshot is None


def test_stale_scheduler_request_cannot_revoke_a_different_selected_case(
    setup: tuple[AssistedUseService, Store, Clock],
) -> None:
    service, _store, _ = setup
    enroll(service)
    service.release()
    selected = service.enroll(OTHER, 7, PASSPHRASE, consent=True)
    assert service.check(CASE) == selected
    assert service.snapshot == selected


@pytest.mark.parametrize("case_id", ("invalid", False, UUID(int=1, version=1)))
def test_scheduler_rejects_invalid_identifiers_even_without_a_selected_case(
    setup: tuple[AssistedUseService, Store, Clock], case_id: object
) -> None:
    service, _store, _ = setup
    with pytest.raises(AssistedUseError) as caught:
        service.check(case_id)  # type: ignore[arg-type]
    assert caught.value.code is AssistedUseErrorCode.INVALID
    assert service.snapshot is None


@pytest.mark.parametrize("version", [0, 2, True, "1"])
def test_old_future_and_noninteger_versions_are_rejected_without_mutation(
    setup: tuple[AssistedUseService, Store, Clock], version: object
) -> None:
    service, store, _ = setup
    enroll(service)
    document = record_to_document(store.records[CASE])
    document["schema_version"] = version
    before = dict(document)
    with pytest.raises(AssistedUseError) as raised:
        record_from_document(document)
    assert raised.value.code is AssistedUseErrorCode.VERSION_UNSUPPORTED
    assert document == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("extra", "PRIVATE_VALUE"),
        ("salt", "wrong"),
        ("verifier", "00"),
        ("retention_days", True),
        ("expired", 1),
        ("consented_at", "2026-11-02"),
        ("case_id", "10000000-0000-1000-8000-000000000001"),
        ("last_seen_at", "2026-11-01T10:00:00+00:00"),
    ],
)
def test_invalid_record_fixtures_fail_safely(
    setup: tuple[AssistedUseService, Store, Clock], field: str, value: object
) -> None:
    service, store, _ = setup
    enroll(service)
    document = record_to_document(store.records[CASE])
    document[field] = value
    with pytest.raises(AssistedUseError) as raised:
        record_from_document(document)
    assert "PRIVATE_VALUE" not in str(raised.value)


def test_domain_disallows_inconsistent_expiry_and_snapshot(
    setup: tuple[AssistedUseService, Store, Clock],
) -> None:
    service, store, _ = setup
    enroll(service)
    with pytest.raises(AssistedUseError):
        replace(store.records[CASE], expires_at=NOW + timedelta(days=8))
    with pytest.raises(AssistedUseError):
        AssistedUseSnapshot(CASE, AssistedUseStatus.ORDINARY, NOW, 7)


def test_errors_sanitize_untrusted_context_and_invalid_code() -> None:
    error = AssistedUseError("unsafe", "C:\\PRIVATE_VALUE")  # type: ignore[arg-type]
    assert error.code is AssistedUseErrorCode.INVALID
    assert error.context == "assisted.use"
    assert "PRIVATE_VALUE" not in str(error)


def test_immediate_gui_lock_and_activity_do_not_wait_for_backend_disk(
    setup: tuple[AssistedUseService, Store, Clock], monkeypatch: pytest.MonkeyPatch
) -> None:
    service, store, clock = setup
    enroll(service)
    entered, resume, gui_done = Event(), Event(), Event()
    original_save = store.save
    errors: list[BaseException] = []

    def slow_save(record: AssistedUseRecord) -> None:
        entered.set()
        assert resume.wait(5)
        original_save(record)

    def backend_check() -> None:
        try:
            service.check()
        except BaseException as error:
            errors.append(error)

    def gui_lock() -> None:
        snapshot = service.lock()
        assert snapshot is not None and snapshot.status is AssistedUseStatus.LOCKED
        assert service.activity() == snapshot
        gui_done.set()

    monkeypatch.setattr(store, "save", slow_save)
    clock.advance(60)
    backend = Thread(target=backend_check)
    gui = Thread(target=gui_lock)
    backend.start()
    try:
        assert entered.wait(2)
        gui.start()
        assert gui_done.wait(1), "GUI privacy gate waited on backend I/O"
    finally:
        resume.set()
        backend.join(5)
        if gui.ident is not None:
            gui.join(5)
    assert not errors
    assert not backend.is_alive() and not gui.is_alive()


def test_an_inflight_unlock_cannot_undo_a_newer_gui_lock(
    setup: tuple[AssistedUseService, Store, Clock], monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _, _ = setup
    enroll(service)
    service.lock()
    entered, resume = Event(), Event()
    derive = assisted_application._derive
    results: list[AssistedUseSnapshot] = []

    def slow_derive(secret: bytes, salt: bytes) -> bytes:
        entered.set()
        assert resume.wait(5)
        return derive(secret, salt)

    monkeypatch.setattr(assisted_application, "_derive", slow_derive)
    backend = Thread(target=lambda: results.append(service.unlock(CASE, PASSPHRASE)))
    backend.start()
    try:
        assert entered.wait(2)
        assert service.lock() is not None
    finally:
        resume.set()
        backend.join(5)
    assert not backend.is_alive()
    assert results[0].status is AssistedUseStatus.LOCKED
    with pytest.raises(AssistedUseError) as raised:
        service.require_access(CASE)
    assert raised.value.code is AssistedUseErrorCode.LOCKED

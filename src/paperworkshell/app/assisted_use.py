"""One-case assisted consent, expiry and local unlock without accounts or services."""

from __future__ import annotations

import hashlib
import hmac
import math
import secrets
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event, Lock, RLock
from time import monotonic
from typing import Protocol, runtime_checkable
from uuid import UUID

from paperworkshell.domain.assisted_use import (
    ASSISTED_USE_CHECKPOINT_SECONDS,
    ASSISTED_USE_IDLE_LOCK_SECONDS,
    ASSISTED_USE_RETENTION_DAYS,
    ASSISTED_USE_UNLOCK_ATTEMPTS,
    ASSISTED_USE_UNLOCK_COOLDOWN_SECONDS,
    MAX_ASSISTED_PASSPHRASE_LENGTH,
    MIN_ASSISTED_PASSPHRASE_LENGTH,
    AssistedUseError,
    AssistedUseErrorCode,
    AssistedUseRecord,
    AssistedUseSnapshot,
    AssistedUseStatus,
    reject_assisted_use,
    require_assisted_case_id,
    require_assisted_timestamp,
)


@runtime_checkable
class AssistedUseStore(Protocol):
    """Atomic protected per-case metadata, independently versioned from document intent."""

    def load(self, case_id: UUID) -> AssistedUseRecord | None: ...

    def save(self, record: AssistedUseRecord) -> None: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _passphrase_bytes(value: object) -> bytes:
    if (
        type(value) is not str
        or not MIN_ASSISTED_PASSPHRASE_LENGTH <= len(value) <= MAX_ASSISTED_PASSPHRASE_LENGTH
        or not value.strip()
    ):
        reject_assisted_use(AssistedUseErrorCode.PASSPHRASE_INVALID, "assisted.passphrase")
    try:
        return value.encode("utf-8", errors="strict")
    except UnicodeError:
        reject_assisted_use(AssistedUseErrorCode.PASSPHRASE_INVALID, "assisted.passphrase")


def _derive(passphrase: bytes, salt: bytes) -> bytes:
    # Fixed by record schema v1: 16 MiB working memory, no external dependency.
    return hashlib.scrypt(passphrase, salt=salt, n=16_384, r=8, p=1, dklen=32, maxmem=32_768_000)


class AssistedUseService:
    """Serialize one assisted-case access grant, with restart always requiring unlock.

    The local passphrase gates application presentation and commands; it does not
    encrypt case files or defend against someone controlling the same OS account.
    ``lock`` only changes memory and is safe to call directly from the GUI thread.
    Disk work and passphrase derivation belong on the backend owner thread.
    """

    def __init__(
        self,
        store: AssistedUseStore,
        *,
        clock: Callable[[], datetime] = _utc_now,
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> None:
        self._store = store
        self._clock = clock
        self._monotonic = monotonic_clock
        self._mutex = RLock()
        self._gate_mutex = Lock()
        self._locked = Event()
        self._lock_epoch = 0
        self._record: AssistedUseRecord | None = None
        self._status = AssistedUseStatus.LOCKED
        self._last_activity = 0.0
        self._last_tick = 0.0
        self._persisted_seen: datetime | None = None
        self._failures = 0
        self._retry_after = 0.0

    @property
    def snapshot(self) -> AssistedUseSnapshot | None:
        """Return current memory-only facts; use check before granting access."""

        return self._snapshot()

    def enroll(
        self,
        case_id: UUID,
        retention_days: int,
        passphrase: str,
        *,
        consent: bool,
    ) -> AssistedUseSnapshot:
        """Persist closed consent and verifier before granting import or content access."""

        selected = require_assisted_case_id(case_id)
        if consent is not True:
            reject_assisted_use(AssistedUseErrorCode.CONSENT_REQUIRED, "assisted.consent")
        if type(retention_days) is not int or retention_days not in ASSISTED_USE_RETENTION_DAYS:
            reject_assisted_use(AssistedUseErrorCode.INVALID, "assisted.retention")
        secret = _passphrase_bytes(passphrase)
        with self._mutex:
            epoch = self._capture_lock_epoch()
            self._require_selected(selected)
            if self._load(selected) is not None:
                reject_assisted_use(AssistedUseErrorCode.ALREADY_ENROLLED, "assisted.enroll")
            now, tick = self._times()
            try:
                salt = secrets.token_bytes(16)
                record = AssistedUseRecord(
                    selected,
                    now,
                    now + timedelta(days=retention_days),
                    now,
                    retention_days,
                    salt,
                    _derive(secret, salt),
                )
            except ValueError, OverflowError, OSError:
                reject_assisted_use(AssistedUseErrorCode.INVALID, "assisted.enroll")
            self._save(record)
            self._record = record
            self._grant_if_current(epoch)
            self._last_activity = self._last_tick = tick
            self._persisted_seen = now
            return self._required_snapshot()

    def inspect(self, case_id: UUID) -> AssistedUseSnapshot:
        """Inspect policy before opening any manifest; never grants a new access lease."""

        selected = require_assisted_case_id(case_id)
        with self._mutex:
            self._require_selected(selected)
            if self._record is not None:
                self._refresh()
                return self._required_snapshot()
            record = self._load(selected)
            if record is None:
                return AssistedUseSnapshot(selected, AssistedUseStatus.ORDINARY, None, None)
            now, _ = self._times()
            updated, status = self._advance(record, now)
            if updated != record:
                self._save(updated)
            # Selecting a protected case establishes isolation before unlocking.
            # Retain its locked context so a later checkpoint failure cannot be
            # mistaken for an ordinary/no-case state by the desktop bridge.
            self._record = updated
            self._status = status
            self._persisted_seen = updated.last_seen_at
            self.lock()
            return self._required_snapshot()

    def unlock(self, case_id: UUID, passphrase: str) -> AssistedUseSnapshot:
        """Check the local passphrase with a bounded, nonblocking retry cooldown."""

        selected = require_assisted_case_id(case_id)
        with self._mutex:
            self._require_selected(selected)
            epoch = self._capture_lock_epoch()
            now, tick = self._times()
            if tick < self._retry_after:
                reject_assisted_use(AssistedUseErrorCode.UNLOCK_WAIT, "assisted.unlock_wait")
            record = self._record if self._record is not None else self._load(selected)
            if record is None:
                reject_assisted_use(AssistedUseErrorCode.INVALID, "assisted.unlock")
            updated, status = self._advance(record, now)
            # Unlock is a durable high-water boundary even when the in-memory
            # record already advanced since the last periodic checkpoint.
            self._save(updated)
            if status in (AssistedUseStatus.EXPIRED, AssistedUseStatus.CLOCK_REVIEW):
                if self._record is not None:
                    self._record, self._status = updated, status
                self._reject_status(status)
            try:
                secret = _passphrase_bytes(passphrase)
                matched = hmac.compare_digest(_derive(secret, updated.salt), updated.verifier)
            except AssistedUseError:
                matched = False
            except ValueError, OSError:
                self._status = AssistedUseStatus.LOCKED
                reject_assisted_use(AssistedUseErrorCode.INVALID, "assisted.unlock")
            if not matched:
                self.lock()
                self._failures += 1
                if self._failures >= ASSISTED_USE_UNLOCK_ATTEMPTS:
                    self._retry_after = tick + ASSISTED_USE_UNLOCK_COOLDOWN_SECONDS
                    self._failures = 0
                reject_assisted_use(AssistedUseErrorCode.UNLOCK_FAILED, "assisted.unlock")
            self._record = updated
            self._persisted_seen = updated.last_seen_at
            self._grant_if_current(epoch)
            self._last_activity = self._last_tick = tick
            self._failures = 0
            self._retry_after = 0.0
            return self._required_snapshot()

    def lock(self, case_id: UUID | None = None) -> AssistedUseSnapshot | None:
        """Hide/deny active content immediately with no filesystem or clock calls."""

        if case_id is not None:
            self._require_selected(require_assisted_case_id(case_id))
        # This gate is never held around disk, callbacks or passphrase derivation.
        # Its epoch prevents an in-flight unlock from undoing a newer UI lock.
        with self._gate_mutex:
            self._lock_epoch += 1
            self._locked.set()
        return self._snapshot()

    def check(self, case_id: UUID | None = None) -> AssistedUseSnapshot | None:
        """Check the selected context without letting stale work select a case."""

        selected = require_assisted_case_id(case_id) if case_id is not None else None
        with self._mutex:
            record = self._record
            if record is None:
                return None
            if selected is not None and selected != record.case_id:
                return self._required_snapshot()
            self._refresh()
            return self._required_snapshot()

    def activity(self) -> AssistedUseSnapshot | None:
        """Touch monotonic input time without disk I/O; never revive an idle lease.

        The GUI calls this for real input. Its independent timer hides the UI on
        wall-clock expiry; the backend scheduler persists clock and expiry state.
        """

        if not self._mutex.acquire(blocking=False):
            return self._snapshot()
        try:
            if self._record is None:
                return None
            try:
                tick = self._monotonic()
                valid_tick = type(tick) in (int, float) and math.isfinite(tick) and tick >= 0
            except Exception:
                valid_tick = False
                tick = 0.0
            if (
                not valid_tick
                or tick < self._last_tick
                or (tick - self._last_activity >= ASSISTED_USE_IDLE_LOCK_SECONDS)
            ):
                self.lock()
            self._reject_status(self._effective_status())
            self._last_activity = self._last_tick = float(tick)
            return self._required_snapshot()
        finally:
            self._mutex.release()

    def require_access(self, case_id: UUID) -> None:
        """Fail closed before document operations and on a different selected case."""

        with self._mutex:
            snapshot = self.inspect(case_id)
            self._reject_status(snapshot.status)

    def release(self) -> None:
        """Checkpoint then relinquish access only after successful backend case close."""

        with self._mutex:
            if self._record is not None:
                self._refresh(force=True)
            self._record = None
            self._persisted_seen = None
            self._status = AssistedUseStatus.LOCKED
            self.lock()

    def _capture_lock_epoch(self) -> int:
        with self._gate_mutex:
            return self._lock_epoch

    def _grant_if_current(self, epoch: int) -> None:
        with self._gate_mutex:
            if epoch == self._lock_epoch:
                self._locked.clear()
                self._status = AssistedUseStatus.UNLOCKED
            else:
                self._status = AssistedUseStatus.LOCKED

    def _effective_status(self) -> AssistedUseStatus:
        status = self._status
        if self._locked.is_set() and status is AssistedUseStatus.UNLOCKED:
            return AssistedUseStatus.LOCKED
        return status

    def _require_selected(self, case_id: UUID) -> None:
        if self._record is not None and self._record.case_id != case_id:
            reject_assisted_use(AssistedUseErrorCode.CASE_CONFLICT, "assisted.case_isolation")

    def _times(self) -> tuple[datetime, float]:
        try:
            now = require_assisted_timestamp(self._clock())
            tick = self._monotonic()
            if type(tick) not in (int, float) or not math.isfinite(tick) or tick < 0:
                reject_assisted_use(AssistedUseErrorCode.INVALID, "assisted.clock")
        except Exception:
            self.lock()
            reject_assisted_use(AssistedUseErrorCode.INVALID, "assisted.clock")
        return now, float(tick)

    def _advance(
        self, record: AssistedUseRecord, now: datetime
    ) -> tuple[AssistedUseRecord, AssistedUseStatus]:
        seen = max(record.last_seen_at, now)
        expired = record.expired or seen >= record.expires_at
        updated = replace(record, last_seen_at=seen, expired=expired)
        status = (
            AssistedUseStatus.EXPIRED
            if expired
            else AssistedUseStatus.CLOCK_REVIEW
            if now < record.last_seen_at
            else AssistedUseStatus.LOCKED
        )
        return updated, status

    def _refresh(self, *, force: bool = False) -> None:
        record = self._record
        if record is None:
            return
        now, tick = self._times()
        updated, status = self._advance(record, now)
        if status in (AssistedUseStatus.EXPIRED, AssistedUseStatus.CLOCK_REVIEW):
            self._status = status
        elif self._status is AssistedUseStatus.CLOCK_REVIEW:
            self._status = AssistedUseStatus.LOCKED
        elif tick < self._last_tick or tick - self._last_activity >= ASSISTED_USE_IDLE_LOCK_SECONDS:
            self._status = AssistedUseStatus.LOCKED
            self.lock()
        self._last_tick = tick
        self._record = updated
        if (
            force
            or updated.expired != record.expired
            or (
                self._persisted_seen is None
                or (updated.last_seen_at - self._persisted_seen).total_seconds()
                >= ASSISTED_USE_CHECKPOINT_SECONDS
            )
        ):
            self._save(updated)
            self._persisted_seen = updated.last_seen_at

    def _load(self, case_id: UUID) -> AssistedUseRecord | None:
        try:
            record = self._store.load(case_id)
            if record is not None and (
                type(record) is not AssistedUseRecord or record.case_id != case_id
            ):
                reject_assisted_use(AssistedUseErrorCode.INVALID, "assisted.load")
            return record
        except AssistedUseError:
            self.lock()
            raise
        except Exception:
            self.lock()
            reject_assisted_use(AssistedUseErrorCode.STORAGE_FAILED, "assisted.load")

    def _save(self, record: AssistedUseRecord) -> None:
        try:
            self._store.save(record)
        except AssistedUseError:
            self.lock()
            raise
        except Exception:
            self.lock()
            reject_assisted_use(AssistedUseErrorCode.STORAGE_FAILED, "assisted.save")

    @staticmethod
    def _project(record: AssistedUseRecord, status: AssistedUseStatus) -> AssistedUseSnapshot:
        return AssistedUseSnapshot(record.case_id, status, record.expires_at, record.retention_days)

    def _snapshot(self) -> AssistedUseSnapshot | None:
        record = self._record
        return None if record is None else self._project(record, self._effective_status())

    def _required_snapshot(self) -> AssistedUseSnapshot:
        snapshot = self._snapshot()
        if snapshot is None:
            reject_assisted_use(AssistedUseErrorCode.INVALID, "assisted.active")
        return snapshot

    @staticmethod
    def _reject_status(status: AssistedUseStatus) -> None:
        failures = {
            AssistedUseStatus.LOCKED: AssistedUseErrorCode.LOCKED,
            AssistedUseStatus.EXPIRED: AssistedUseErrorCode.EXPIRED,
            AssistedUseStatus.CLOCK_REVIEW: AssistedUseErrorCode.CLOCK_REVIEW,
        }
        if status in failures:
            reject_assisted_use(failures[status], "assisted.access")

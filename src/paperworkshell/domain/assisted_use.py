"""Versioned, content-free consent and local-lock policy for an assisted case."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final, Never
from uuid import UUID

ASSISTED_USE_SCHEMA_VERSION: Final = 1
ASSISTED_USE_CONSENT_VERSION: Final = 1
ASSISTED_USE_RETENTION_DAYS: Final = (1, 7, 30)
ASSISTED_USE_IDLE_LOCK_SECONDS: Final = 300
MIN_ASSISTED_PASSPHRASE_LENGTH: Final = 12
MAX_ASSISTED_PASSPHRASE_LENGTH: Final = 128
ASSISTED_USE_CHECKPOINT_SECONDS: Final = 60
ASSISTED_USE_UNLOCK_ATTEMPTS: Final = 5
ASSISTED_USE_UNLOCK_COOLDOWN_SECONDS: Final = 30
_CONTEXT: Final = re.compile(r"[a-z][a-z0-9_.]{0,63}\Z")


class AssistedUseErrorCode(StrEnum):
    """Closed failures that do not carry credentials or document values."""

    INVALID = "ASSISTED_USE_INVALID"
    VERSION_UNSUPPORTED = "ASSISTED_USE_VERSION_UNSUPPORTED"
    CONSENT_REQUIRED = "ASSISTED_USE_CONSENT_REQUIRED"
    PASSPHRASE_INVALID = "ASSISTED_USE_PASSPHRASE_INVALID"
    UNLOCK_FAILED = "ASSISTED_USE_UNLOCK_FAILED"
    UNLOCK_WAIT = "ASSISTED_USE_UNLOCK_WAIT"
    LOCKED = "ASSISTED_USE_LOCKED"
    EXPIRED = "ASSISTED_USE_EXPIRED"
    CLOCK_REVIEW = "ASSISTED_USE_CLOCK_REVIEW"
    CASE_CONFLICT = "ASSISTED_USE_CASE_CONFLICT"
    ALREADY_ENROLLED = "ASSISTED_USE_ALREADY_ENROLLED"
    STORAGE_FAILED = "ASSISTED_USE_STORAGE_FAILED"


_MESSAGES: Final = {
    AssistedUseErrorCode.INVALID: "The assisted-use record is invalid and was not changed.",
    AssistedUseErrorCode.VERSION_UNSUPPORTED: (
        "This assisted-use record version is not supported and was not changed."
    ),
    AssistedUseErrorCode.CONSENT_REQUIRED: (
        "Confirm consent and the retention explanation before adding documents."
    ),
    AssistedUseErrorCode.PASSPHRASE_INVALID: "Choose a passphrase with 12 to 128 characters.",
    AssistedUseErrorCode.UNLOCK_FAILED: "The passphrase did not unlock this case. Try again.",
    AssistedUseErrorCode.UNLOCK_WAIT: "Wait 30 seconds before trying to unlock again.",
    AssistedUseErrorCode.LOCKED: "This assisted case is locked. Unlock it to continue.",
    AssistedUseErrorCode.EXPIRED: (
        "This assisted case has expired. Its files remain in case storage until explicit removal."
    ),
    AssistedUseErrorCode.CLOCK_REVIEW: (
        "The computer clock moved backwards. Correct the clock, then unlock the case."
    ),
    AssistedUseErrorCode.CASE_CONFLICT: "Close this assisted case before selecting another case.",
    AssistedUseErrorCode.ALREADY_ENROLLED: "This case already has an assisted-use policy.",
    AssistedUseErrorCode.STORAGE_FAILED: (
        "The assisted-use policy could not be saved or read safely. The case remains locked."
    ),
}


@dataclass(slots=True, repr=False)
class AssistedUseError(ValueError):
    """Sanitized error safe for UI and routine diagnostics."""

    code: AssistedUseErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not AssistedUseErrorCode:
            self.code = AssistedUseErrorCode.INVALID
        if type(self.context) is not str or _CONTEXT.fullmatch(self.context) is None:
            self.context = "assisted.use"
        self.safe_message = _MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.safe_message)

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"

    def __repr__(self) -> str:
        return f"AssistedUseError(code={self.code.value!r}, context={self.context!r})"


def reject_assisted_use(code: AssistedUseErrorCode, context: str) -> Never:
    """Raise one closed failure, suppressing raw exception chains."""

    raise AssistedUseError(code, context) from None


def require_assisted_case_id(value: object) -> UUID:
    """Accept opaque UUIDv4 case identities only."""

    if type(value) is not UUID or value.version != 4:
        reject_assisted_use(AssistedUseErrorCode.INVALID, "assisted.case_id")
    return value


def require_assisted_timestamp(value: object) -> datetime:
    """Require an aware, zero-offset datetime without coercion."""

    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
        reject_assisted_use(AssistedUseErrorCode.INVALID, "assisted.timestamp")
    return value


class AssistedUseStatus(StrEnum):
    """Application access state; expiry quarantines rather than silently deleting."""

    ORDINARY = "ordinary"
    UNLOCKED = "unlocked"
    LOCKED = "locked"
    EXPIRED = "expired"
    CLOCK_REVIEW = "clock_review"


@dataclass(frozen=True, slots=True)
class AssistedUseRecord:
    """Protected local metadata; never include the verifier in a UI projection."""

    case_id: UUID
    consented_at: datetime
    expires_at: datetime
    last_seen_at: datetime
    retention_days: int
    salt: bytes = field(repr=False)
    verifier: bytes = field(repr=False)
    expired: bool = False
    schema_version: int = ASSISTED_USE_SCHEMA_VERSION
    consent_version: int = ASSISTED_USE_CONSENT_VERSION

    def __post_init__(self) -> None:
        require_assisted_case_id(self.case_id)
        if (
            type(self.schema_version) is not int
            or self.schema_version != ASSISTED_USE_SCHEMA_VERSION
            or type(self.consent_version) is not int
            or self.consent_version != ASSISTED_USE_CONSENT_VERSION
        ):
            reject_assisted_use(AssistedUseErrorCode.VERSION_UNSUPPORTED, "assisted.version")
        for timestamp in (self.consented_at, self.expires_at, self.last_seen_at):
            require_assisted_timestamp(timestamp)
        if (
            type(self.retention_days) is not int
            or self.retention_days not in ASSISTED_USE_RETENTION_DAYS
            or type(self.expired) is not bool
            or type(self.salt) is not bytes
            or len(self.salt) != 16
            or type(self.verifier) is not bytes
            or len(self.verifier) != 32
            or self.expires_at - self.consented_at != timedelta(days=self.retention_days)
            or self.last_seen_at < self.consented_at
            or (self.last_seen_at >= self.expires_at and not self.expired)
        ):
            reject_assisted_use(AssistedUseErrorCode.INVALID, "assisted.record")


@dataclass(frozen=True, slots=True)
class AssistedUseSnapshot:
    """Content-free facts allowed to cross into presentation code."""

    case_id: UUID
    status: AssistedUseStatus
    expires_at: datetime | None
    retention_days: int | None

    def __post_init__(self) -> None:
        require_assisted_case_id(self.case_id)
        if type(self.status) is not AssistedUseStatus:
            reject_assisted_use(AssistedUseErrorCode.INVALID, "assisted.snapshot")
        if self.status is AssistedUseStatus.ORDINARY:
            if self.expires_at is not None or self.retention_days is not None:
                reject_assisted_use(AssistedUseErrorCode.INVALID, "assisted.snapshot")
        else:
            require_assisted_timestamp(self.expires_at)
            if type(self.retention_days) is not int or self.retention_days not in (
                ASSISTED_USE_RETENTION_DAYS
            ):
                reject_assisted_use(AssistedUseErrorCode.INVALID, "assisted.snapshot")


def record_to_document(record: AssistedUseRecord) -> dict[str, object]:
    """Encode a validated current record as closed versioned local data."""

    if type(record) is not AssistedUseRecord:
        reject_assisted_use(AssistedUseErrorCode.INVALID, "assisted.encode")
    return {
        "schema_version": record.schema_version,
        "consent_version": record.consent_version,
        "case_id": str(record.case_id),
        "consented_at": record.consented_at.astimezone(UTC).isoformat(),
        "expires_at": record.expires_at.astimezone(UTC).isoformat(),
        "last_seen_at": record.last_seen_at.astimezone(UTC).isoformat(),
        "retention_days": record.retention_days,
        "salt": record.salt.hex(),
        "verifier": record.verifier.hex(),
        "expired": record.expired,
    }


def record_from_document(document: object) -> AssistedUseRecord:
    """Reject old/future versions, coercion, unknown fields and malformed values."""

    keys = {
        "schema_version",
        "consent_version",
        "case_id",
        "consented_at",
        "expires_at",
        "last_seen_at",
        "retention_days",
        "salt",
        "verifier",
        "expired",
    }
    if type(document) is not dict or document.keys() != keys:
        reject_assisted_use(AssistedUseErrorCode.INVALID, "assisted.decode")
    for key in ("schema_version", "consent_version"):
        if type(document[key]) is not int or document[key] != 1:
            reject_assisted_use(AssistedUseErrorCode.VERSION_UNSUPPORTED, "assisted.version")
    if (
        any(
            type(document[key]) is not str
            for key in ("case_id", "consented_at", "expires_at", "last_seen_at", "salt", "verifier")
        )
        or type(document["retention_days"]) is not int
        or type(document["expired"]) is not bool
    ):
        reject_assisted_use(AssistedUseErrorCode.INVALID, "assisted.decode")
    try:
        case_id = UUID(document["case_id"])
        timestamps = tuple(
            datetime.fromisoformat(document[key])
            for key in ("consented_at", "expires_at", "last_seen_at")
        )
        salt = bytes.fromhex(document["salt"])
        verifier = bytes.fromhex(document["verifier"])
        record = AssistedUseRecord(
            case_id,
            timestamps[0],
            timestamps[1],
            timestamps[2],
            document["retention_days"],
            salt,
            verifier,
            document["expired"],
            document["schema_version"],
            document["consent_version"],
        )
    except ValueError, TypeError, OverflowError:
        reject_assisted_use(AssistedUseErrorCode.INVALID, "assisted.decode")
    if record_to_document(record) != document:
        reject_assisted_use(AssistedUseErrorCode.INVALID, "assisted.canonical")
    return record

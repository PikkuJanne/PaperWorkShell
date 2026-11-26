"""Authorize one update channel's metadata key from its offline trust root."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final, TypeGuard, cast

from paperworkshell.domain.product_identity import INSTALLED_APPLICATION_ID
from paperworkshell.domain.update_verification import SIGNATURE_PREFIX
from paperworkshell.storage._update_cng import (
    P256_PUBLIC_KEY_BYTES,
    is_p256_public_key,
    verify_p256_signature,
)

TRUST_SIGNATURE_PREFIX: Final = b"PaperWorkShell update trust v1\x00"
MAX_TRUST_RECORD_BYTES: Final = 4096
_MAX_COUNTER: Final = (1 << 63) - 1
_RECORD_KEYS: Final = frozenset(
    {
        "schema_version",
        "application_id",
        "platform",
        "channel",
        "epoch",
        "issued_at",
        "expires_at",
        "release_sequence_floor",
        "metadata_public_key",
    }
)
_CHANNEL: Final = re.compile(r"[a-z][a-z0-9-]{0,31}")
_UTC_TIME: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_SHA256: Final = re.compile(r"[0-9a-f]{64}")
_PUBLIC_KEY: Final = re.compile(r"[0-9a-f]{128}")


class TrustErrorCode(StrEnum):
    RECORD_INVALID = "UPDATE_TRUST_RECORD_INVALID"
    SIGNATURE_INVALID = "UPDATE_TRUST_SIGNATURE_INVALID"
    TARGET_MISMATCH = "UPDATE_TRUST_TARGET_MISMATCH"
    RECORD_NOT_CURRENT = "UPDATE_TRUST_RECORD_NOT_CURRENT"
    ROLLBACK_FORBIDDEN = "UPDATE_TRUST_ROLLBACK_FORBIDDEN"


_MESSAGES: Final = {
    TrustErrorCode.RECORD_INVALID: "The PaperWorkShell update trust information is invalid.",
    TrustErrorCode.SIGNATURE_INVALID: "The update trust signature could not be verified.",
    TrustErrorCode.TARGET_MISMATCH: "This update trust information is for a different target.",
    TrustErrorCode.RECORD_NOT_CURRENT: "The PaperWorkShell update trust information is out of date.",
    TrustErrorCode.ROLLBACK_FORBIDDEN: "This update trust information has been superseded.",
}


class UpdateTrustError(ValueError):
    """A stable trust-policy failure with no supplied values or paths."""

    def __init__(self, code: TrustErrorCode) -> None:
        self.code = code
        super().__init__(_MESSAGES[code])


@dataclass(frozen=True, slots=True)
class TrustAnchor:
    """Release-provisioned root and minimum trust epoch for one channel."""

    channel: str
    root_public_key: bytes = field(repr=False)
    minimum_epoch: int = 1

    def __post_init__(self) -> None:
        if (
            _CHANNEL.fullmatch(self.channel) is None
            or type(self.root_public_key) is not bytes
            or len(self.root_public_key) != P256_PUBLIC_KEY_BYTES
            or not _counter(self.minimum_epoch, minimum=1)
        ):
            raise ValueError("Invalid provisioned update trust anchor.")


@dataclass(frozen=True, slots=True)
class TrustCheckpoint:
    """The epoch and exact signed record digest retained by trusted local storage."""

    epoch: int = 0
    record_sha256: str | None = None

    def __post_init__(self) -> None:
        digest = self.record_sha256
        if (
            not _counter(self.epoch)
            or (digest is None and self.epoch != 0)
            or (digest is not None and (self.epoch == 0 or _SHA256.fullmatch(digest) is None))
        ):
            raise ValueError("Invalid update trust checkpoint.")


@dataclass(frozen=True, slots=True)
class ChannelTrust:
    """Authorize a metadata signature under the checked trust record."""

    channel: str
    checkpoint: TrustCheckpoint
    issued_at: datetime
    expires_at: datetime
    release_sequence_floor: int
    _metadata_public_key: bytes | None = field(repr=False)

    @property
    def revoked(self) -> bool:
        return self._metadata_public_key is None

    def permit_update_signature(self, message: bytes, signature: bytes) -> bool:
        key = self._metadata_public_key
        return (
            key is not None
            and message.startswith(SIGNATURE_PREFIX)
            and verify_p256_signature(key, message, signature)
        )


def verify_channel_trust(
    record: bytes,
    signature: bytes,
    *,
    anchor: TrustAnchor,
    checkpoint: TrustCheckpoint,
    now: datetime,
) -> ChannelTrust:
    """Verify an offline authorization against installed, independently provisioned trust.

    Evaluate this before each update check or installation. Persist the returned
    checkpoint independently of whether an update succeeds, including revocations.
    """
    if now.utcoffset() != timedelta(0):
        raise ValueError("Update trust requires a UTC evaluation time.")
    if not 0 < len(record) <= MAX_TRUST_RECORD_BYTES:
        raise UpdateTrustError(TrustErrorCode.RECORD_INVALID)
    if not verify_p256_signature(
        anchor.root_public_key, TRUST_SIGNATURE_PREFIX + record, signature
    ):
        raise UpdateTrustError(TrustErrorCode.SIGNATURE_INVALID)

    payload = _parse_record(record)
    if (
        payload["application_id"] != INSTALLED_APPLICATION_ID
        or payload["platform"] != "win-x64"
        or payload["channel"] != anchor.channel
    ):
        raise UpdateTrustError(TrustErrorCode.TARGET_MISMATCH)

    epoch = payload["epoch"]
    sequence_floor = payload["release_sequence_floor"]
    if not _counter(epoch, minimum=1) or not _counter(sequence_floor):
        raise UpdateTrustError(TrustErrorCode.RECORD_INVALID)
    accepted_checkpoint = TrustCheckpoint(epoch, hashlib.sha256(record).hexdigest())
    if accepted_checkpoint.epoch < max(anchor.minimum_epoch, checkpoint.epoch) or (
        accepted_checkpoint.epoch == checkpoint.epoch and accepted_checkpoint != checkpoint
    ):
        raise UpdateTrustError(TrustErrorCode.ROLLBACK_FORBIDDEN)

    issued_at = _utc_time(payload["issued_at"])
    expires_at = _utc_time(payload["expires_at"])
    if expires_at <= issued_at:
        raise UpdateTrustError(TrustErrorCode.RECORD_INVALID)
    if not issued_at <= now < expires_at:
        raise UpdateTrustError(TrustErrorCode.RECORD_NOT_CURRENT)

    key = _metadata_key(payload["metadata_public_key"], anchor)
    return ChannelTrust(
        anchor.channel,
        accepted_checkpoint,
        issued_at,
        expires_at,
        sequence_floor,
        key,
    )


def _counter(value: object, *, minimum: int = 0) -> TypeGuard[int]:
    return type(value) is int and minimum <= value <= _MAX_COUNTER


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate update trust field.")
        result[key] = value
    return result


def _parse_record(record: bytes) -> dict[str, object]:
    try:
        parsed: object = json.loads(record.decode("utf-8"), object_pairs_hook=_unique_object)
    except ValueError, RecursionError:
        raise UpdateTrustError(TrustErrorCode.RECORD_INVALID) from None
    if not isinstance(parsed, dict) or parsed.keys() != _RECORD_KEYS:
        raise UpdateTrustError(TrustErrorCode.RECORD_INVALID)
    payload = cast("dict[str, object]", parsed)
    schema = payload["schema_version"]
    if type(schema) is not int or schema != 1:
        raise UpdateTrustError(TrustErrorCode.RECORD_INVALID)
    return payload


def _utc_time(value: object) -> datetime:
    if type(value) is not str or _UTC_TIME.fullmatch(value) is None:
        raise UpdateTrustError(TrustErrorCode.RECORD_INVALID)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise UpdateTrustError(TrustErrorCode.RECORD_INVALID) from None


def _metadata_key(value: object, anchor: TrustAnchor) -> bytes | None:
    if value is None:
        return None
    if type(value) is not str or _PUBLIC_KEY.fullmatch(value) is None:
        raise UpdateTrustError(TrustErrorCode.RECORD_INVALID)
    key = bytes.fromhex(value)
    if key == anchor.root_public_key or not is_p256_public_key(key):
        raise UpdateTrustError(TrustErrorCode.RECORD_INVALID)
    return key
